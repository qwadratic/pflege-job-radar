#!/usr/bin/env python
"""Record, extend, diff and inspect the local mirror of the clinic sites (TASK-197; read tests/mirror.py first).

The ONLY code that talks to a clinic site on purpose -- a person or an agent runs it, tests never do (conftest.py blocks every
non-local socket in a test). It reads the registry through the keyless read proxy, runs the production adapter path for a
board (app.crawl._vendor_rows / _seed_obs, group_portal_for first -- exactly what tests/test_adapter_completeness.py runs)
and records every HTTP exchange, hop by hop. No DB writes, no Firecrawl, no LLM: a request to our own infrastructure or a paid
API is refused. The adapters' own sleeps stay while recording and the run is one board at a time.

  record <board_id | host | clinic_id> [...]   re-record whole boards (replaces the file, the old one stays as .prev)
  record --all [--only-missing] [--retry-errors]   every board of the registry, biggest first, disk checked before each
  add <board_id> <url> [--method POST --data FILE] [--scope S] [--via urllib]   one more page, without re-walking the board
  diff <board_id> [--apply]                    record into memory, print added / removed / changed URLs, keep only on --apply
  status [--older-than DAYS]                   what is mirrored (age is information, never an automatic refresh)
  list-urls <board_id> [--scope S]  |  show <board_id> <url>  |  sql <board_id> "<select ...>"  |  reindex
"""
import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from tests import adapter_contract as AC  # noqa: E402
from tests import adapter_harness as H  # noqa: E402
from tests import mirror as M  # noqa: E402

VOLATILE = {"observed_at", "details_fetched_at", "inbox_id"}


def _log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------------------------
# registry -> boards -> stable ids
# ---------------------------------------------------------------------------------------------
def registry():
    """(boards biggest-first, ids parallel to them, towns, board ids that get their mutation runs recorded). Read-only."""
    from pflege_jobs.classify import norm_text
    clinics = AC.live_clinics()
    boards = sorted(AC.boards(clinics).values(), key=lambda b: -len(b["clinics"]))
    idx = M.read_index_or_none() or {"boards": {}}
    by_url = {e["board"]["url"]: bid for bid, e in idx["boards"].items()}
    taken = set(by_url.values())
    ids = []
    for b, fresh in zip(boards, H.assign_ids(boards)):
        if b["url"] in by_url:  # a board keeps the id it was recorded under
            ids.append(by_url[b["url"]])
            continue
        bid, n = fresh, 0
        while bid in taken:
            n += 1
            bid = f"{fresh}-{n}"
        taken.add(bid)
        ids.append(bid)
    towns = sorted({norm_text(c["town"]) for c in clinics if c.get("town")})
    id_of = {b["url"]: bid for b, bid in zip(boards, ids)}
    # the order the tests use (biggest first, ties by id): the first CANDIDATES boards of a family may be its mutation representative
    ordered = sorted(boards, key=lambda b: (-len(b["clinics"]), id_of[b["url"]]))
    candidates = {id_of[b["url"]] for fam in H.family_boards(ordered).values() for b in fam[:H.CANDIDATES]}
    return boards, ids, towns, candidates


def resolve(tokens, boards, ids):
    out = []
    for t in tokens:
        hit = [(b, bid) for b, bid in zip(boards, ids)
               if t == bid or t.removeprefix("www.") == H.board_host(b["url"]) or t == b["url"] or any(c["clinic_id"] == t for c in b["clinics"])]
        if not hit:
            sys.exit(f"no board of the registry matches {t!r} (a board_id, a board host, a board url or a clinic_id)")
        out += [x for x in hit if x not in out]
    return out


# ---------------------------------------------------------------------------------------------
# one board
# ---------------------------------------------------------------------------------------------
def scenario(board):
    """What test_adapter_completeness runs for one board: the adapter, the oracle, the five checks (the round trip fetches)."""
    rows, calls = H.run_adapter(board)
    client = H.client_for(board, cache=False)
    return {"rows": rows, "calls": calls, "client": client, "checks": H.run_checks(board, rows, calls, client)}


def canon(res):
    """A scenario result as comparable text: rows without their timestamps, calls and check verdicts sorted."""
    def clean(v):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                return v
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k not in VOLATILE}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    return {"rows": sorted(json.dumps(clean(r), sort_keys=True, default=str, ensure_ascii=False) for r in res["rows"]),
            "calls": sorted(res["calls"]), "checks": {k: list(v) for k, v in res["checks"].items()}}


def compare(a, b):
    ca, cb = canon(a), canon(b)
    if ca == cb:
        return True, None
    bits = []
    ra, rb = set(ca["rows"]), set(cb["rows"])
    if ra != rb:
        bits.append(f"rows only in the live run {len(ra - rb)}, only in the replay {len(rb - ra)}")
    if ca["calls"] != cb["calls"]:
        bits.append(f"adapter calls differ ({len(ca['calls'])} live, {len(cb['calls'])} replay)")
    bits += [f"check {k}: {ca['checks'][k]} -> {cb['checks'][k]}" for k in ca["checks"] if ca["checks"][k] != cb["checks"].get(k)]
    return False, "; ".join(bits)[:1500]


def record_mutations(rec, board):
    """The four breakages of tests/adapter_harness.MUTATION_APPLIERS on a mutation candidate: what the adapter asks for when
    a read path is cut off may be a fallback page the baseline never fetched. Served from what is stored, live only for the rest."""
    from _pytest.monkeypatch import MonkeyPatch
    rec.mode, out = "through", {}
    try:
        for name in AC.MUTATIONS:
            with MonkeyPatch.context() as mp:
                H.MUTATION_APPLIERS[name](mp, board)
                try:
                    out[name] = len(H.run_adapter(board, scope=f"mutation:{name}")[0])
                except Exception as e:
                    out[name] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        rec.mode = "record"
    return out


def record_one(board, bid, towns, mutations, verify=True):
    """Record one board live. -> (recording, index entry). The caller saves it (or, for `diff`, does not)."""
    board = dict(board, board_id=bid)
    t0, err, res, mut = time.time(), None, None, None
    rec = None
    try:
        with M.recording(bid) as rec:
            rec.store.set_meta("board", board)
            rec.store.set_meta("towns", towns)
            res = scenario(board)
            if mutations:
                mut = record_mutations(rec, board)
    except Exception:
        err = traceback.format_exc()[-1800:]
    seconds = round(time.time() - t0, 1)
    identical = diff = None
    if res is not None and verify:
        try:  # the recording, replayed from memory, must give the very run it was recorded from
            with M.replaying(rec.store):
                identical, diff = compare(res, scenario(board))
        except Exception as e:
            identical, diff = False, f"replay raised {type(e).__name__}: {str(e)[:600]}"
    st = rec.store.stats()
    client = (res or {}).get("client") or {}
    entry = {
        "board_id": bid, "url": board["url"], "vendor": board["vendor"], "kind": board["kind"], "adapter": board.get("adapter"),
        "walled": board.get("walled"), "n_clinics": len(board["clinics"]), "board": board,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "recorder_sha": M.git_sha(), "seconds": seconds,
        **st, "rows": len(res["rows"]) if res else None,
        "declared_total": AC.declared_total(client.get("html"), client.get("api_json")) if client and not client.get("error") else None,
        "checks": {k: v[0] for k, v in res["checks"].items()} if res else None,
        "replay_identical": identical, "replay_diff": diff, "mutations": mut, "error": err, "refused": rec.refused,
    }
    rec.store.set_meta("index", entry)
    return rec, entry


def save_one(rec, entry):
    path = rec.save()
    entry = dict(entry, bytes_file=path.stat().st_size)
    M.update_index(entry)
    return entry


def line(i, n, e):
    ok = e["checks"] and sum(e["checks"].values())
    rep = {True: "replay identical", False: "REPLAY DIFFERS", None: "replay not checked"}[e["replay_identical"]]
    return (f"[{i}/{n}] {e['board_id']}  clinics {e['n_clinics']}  pages {e['pages']}  raw {e['bytes_raw'] / 1e6:.1f} MB"
            f"  xz {e.get('bytes_file', 0) / 1e6:.2f} MB  rows {e['rows']}  checks {ok}/{len(e['checks']) if e['checks'] else '-'}  {rep}"
            f"  exc {e['n_exc']}  {e['seconds']} s" + (f"  ERROR {e['error'].strip().splitlines()[-1][:160]}" if e["error"] else ""))


def free_gb():
    d = M.mirror_dir()
    d.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(d).free / 1e9


# ---------------------------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------------------------
def cmd_record(a):
    if os.environ.get("MIRROR_RECORD"):
        sys.exit("unset MIRROR_RECORD: the tool records fresh, it does not go through a store")
    boards, ids, towns, cands = registry()
    idx = (M.read_index_or_none() or {"boards": {}})["boards"]
    pairs = list(zip(boards, ids)) if a.all else resolve(a.targets, boards, ids)
    if a.all and a.only_missing:
        pairs = [(b, bid) for b, bid in pairs if bid not in idx or (a.retry_errors and idx[bid].get("error"))]
    failed, n = [], len(pairs)
    for i, (b, bid) in enumerate(pairs, 1):
        if free_gb() < a.min_free_gb:
            _log(f"STOP: {free_gb():.2f} GB free under {M.mirror_dir()}, below --min-free-gb {a.min_free_gb}. "
                 f"Recorded {i - 1}/{n} this run; nothing deleted, nothing skipped. Free disk space and rerun with --only-missing.")
            sys.exit(3)
        rec, e = record_one(b, bid, towns, bid in cands, verify=not a.no_verify)
        e = save_one(rec, e)
        _log(line(i, n, e))
        if e["error"] or e["replay_identical"] is False or e["refused"]:
            failed.append(e)
    _log(f"done {n} board(s); {len(failed)} with an error, a replay that differs or a refused request")
    for e in failed:
        _log(f"  {e['board_id']}: " + (e["error"].strip().splitlines()[-1][:200] if e["error"] else e["replay_diff"] or f"refused {e['refused'][:2]}"))


def cmd_add(a):
    import requests
    import urllib.request
    store = M.Store.load(a.board_id)
    m = M.Mirror(a.board_id, store, "record")
    m.scope = a.scope
    body = Path(a.data).read_bytes() if a.data else None
    hdr = {"User-Agent": AC.UA, "Accept-Language": "de-DE,de;q=0.9"}
    with M._activate(m):
        if a.via == "urllib":
            with urllib.request.urlopen(urllib.request.Request(a.url, data=body, method=a.method, headers=hdr), timeout=60) as r:
                st = r.status
        else:
            st = requests.Session().request(a.method, a.url, data=body, headers=hdr, timeout=60).status_code
    store.save()
    _log(f"added {a.method} {a.url} -> {st} ({m.live_requests} request(s) recorded in scope {a.scope}); {store.count()} pages now")
    e = dict(M.read_index()["boards"][a.board_id], pages=store.count(), **{k: v for k, v in store.stats().items() if k != "pages"})
    e["bytes_file"] = M.board_file(a.board_id).stat().st_size
    M.update_index(e)


def cmd_diff(a):
    boards, ids, towns, cands = registry()
    (b, bid), = resolve([a.board_id], boards, ids)
    old = M.Store.load(bid)
    rec, e = record_one(b, bid, towns, bid in cands)

    def table(s):
        t = {}
        for r in s.rows():
            t.setdefault((r.via, r.method, r.url, r.req_sha), []).append(r.body_sha or json.dumps(r.exc))
        return t
    o, n = table(old), table(rec.store)
    added, removed = sorted(set(n) - set(o)), sorted(set(o) - set(n))
    changed = sorted(k for k in set(o) & set(n) if o[k] != n[k])
    _log(f"{bid}: {len(o)} urls recorded before, {len(n)} now: {len(added)} added, {len(removed)} removed, {len(changed)} changed")
    for tag, ks in (("+", added), ("-", removed), ("~", changed)):
        for k in ks[:None if a.full else 20]:
            _log(f"  {tag} {k[1]} {k[2]}")
        if len(ks) > 20 and not a.full:
            _log(f"  {tag} ... {len(ks) - 20} more (--full)")
    if a.apply:
        _log(line(1, 1, save_one(rec, e)))
    else:
        _log("not kept (rerun with --apply to replace the stored mirror)")


def _entries():
    return M.read_index()["boards"]


def cmd_status(a):
    idx = _entries()
    now = datetime.now(timezone.utc)
    rows, tot = [], {"pages": 0, "raw": 0, "file": 0}
    for bid, e in sorted(idx.items(), key=lambda kv: kv[0]):
        age = (now - datetime.fromisoformat(e["recorded_at"])).days
        tot["pages"] += e["pages"]
        tot["raw"] += e["bytes_raw"]
        tot["file"] += e.get("bytes_file", 0)
        flags = [f for f, on in (("ERROR", e.get("error")), ("REPLAY-DIFFERS", e.get("replay_identical") is False), ("EXC", e.get("n_exc")),
                                 (f"OLD>{a.older_than}d", age > a.older_than)) if on]
        rows.append(f"{bid:56.56} {e['kind']:6} clin {e['n_clinics']:2} pages {e['pages']:5} raw {e['bytes_raw'] / 1e6:7.1f} MB "
                    f"xz {e.get('bytes_file', 0) / 1e6:6.2f} MB rows {str(e['rows']):>5} age {age:3}d {e['recorded_at'][:10]}  {' '.join(flags)}")
    _log("\n".join(rows))
    _log(f"{len(idx)} boards, {tot['pages']} pages, {tot['raw'] / 1e9:.2f} GB raw bodies, {tot['file'] / 1e6:.1f} MB on disk; "
         f"'OLD' is information only: nothing refreshes a mirror but a person running `record`")


def cmd_list_urls(a):
    for r in M.Store.load(a.board_id).rows():
        if a.scope in (None, r.scope):
            _log(f"{r.seq:6} {r.scope:22.22} {r.via:10} {r.method:5} {r.status or 'EXC':>4} {r.url}")


def cmd_show(a):
    s = M.Store.load(a.board_id)
    hits = [r for r in s.rows() if r.url == a.url or r.norm == M._norm_url(a.url)]
    if not hits:
        sys.exit(f"{a.board_id} holds nothing for {a.url}")
    for r in hits:
        _log(f"--- seq {r.seq} scope {r.scope} via {r.via} {r.method} {r.url} -> {r.status} {r.reason} ({r.fetched_at})")
        for k, v in r.headers or []:
            _log(f"  {k}: {v}")
        if r.exc:
            _log(f"  EXC {r.exc}")
        elif r.body_sha:
            body = s.body(r.body_sha)
            _log(f"  body {len(body)} bytes sha256 {r.body_sha[:12]}\n" + body.decode("utf-8", "replace")[:None if a.full else 3000])


def cmd_sql(a):
    for row in M.Store.load(a.board_id).db.execute(a.query):
        _log("\t".join(str(c)[:200] for c in row))


def cmd_reindex(a):
    n = 0
    for f in sorted(M.mirror_dir().glob("*.sqlite.xz")):
        s = M.Store.load(f.name.removesuffix(".sqlite.xz"))
        e = s.meta("index")
        if not e:
            _log(f"{f.name}: no index entry in its meta (recorded by something else?), skipped")
            continue
        M.update_index(dict(e, bytes_file=f.stat().st_size))
        n += 1
    _log(f"INDEX.json rebuilt from {n} board file(s)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("targets", nargs="*")
    r.add_argument("--all", action="store_true")
    r.add_argument("--only-missing", action="store_true", help="with --all: skip boards that already have a mirror")
    r.add_argument("--retry-errors", action="store_true", help="with --only-missing: also redo boards whose recording ended in an error")
    r.add_argument("--min-free-gb", type=float, default=1.0)
    r.add_argument("--no-verify", action="store_true", help="skip the replay-equals-recording check")
    r.set_defaults(fn=cmd_record)
    d = sub.add_parser("add")
    d.add_argument("board_id")
    d.add_argument("url")
    d.add_argument("--method", default="GET")
    d.add_argument("--data", help="file holding the request body")
    d.add_argument("--scope", default="adapter")
    d.add_argument("--via", choices=("requests", "urllib"), default="requests")
    d.set_defaults(fn=cmd_add)
    f = sub.add_parser("diff")
    f.add_argument("board_id")
    f.add_argument("--apply", action="store_true")
    f.add_argument("--full", action="store_true")
    f.set_defaults(fn=cmd_diff)
    s = sub.add_parser("status")
    s.add_argument("--older-than", type=int, default=30)
    s.set_defaults(fn=cmd_status)
    l = sub.add_parser("list-urls")
    l.add_argument("board_id")
    l.add_argument("--scope")
    l.set_defaults(fn=cmd_list_urls)
    w = sub.add_parser("show")
    w.add_argument("board_id")
    w.add_argument("url")
    w.add_argument("--full", action="store_true")
    w.set_defaults(fn=cmd_show)
    q = sub.add_parser("sql")
    q.add_argument("board_id")
    q.add_argument("query")
    q.set_defaults(fn=cmd_sql)
    x = sub.add_parser("reindex")
    x.set_defaults(fn=cmd_reindex)
    a = p.parse_args()
    if a.cmd == "record" and not (a.all or a.targets):
        p.error("record needs board ids/hosts/clinic ids, or --all")
    a.fn(a)


if __name__ == "__main__":
    main()
