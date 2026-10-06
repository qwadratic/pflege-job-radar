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
  record-infra                                 the registry read proxy snapshot behind tests/test_geo.py (our own DB, read-only); written to
                                               tests/fixtures/mirror_infra/ (committed, headers cut), not to the mirror root
  push                                         the mirror to the private Bunny Storage Zone: one tar, then latest.json (env BUNNY_MIRROR_ZONE, BUNNY_MIRROR_RW_KEY)
  pull                                         the mirror from there into the mirror root (env BUNNY_MIRROR_ZONE, BUNNY_MIRROR_RO_KEY); docs/deploy.md
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import requests

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
    stats = {}
    rows, calls = H.run_adapter(board, stats=stats)
    client = H.client_for(board, cache=False)
    return {"rows": rows, "calls": calls, "client": client, "checks": H.run_checks(board, rows, calls, client),
            "adapter_error": stats.get("error")}


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
        "adapter_error": (res or {}).get("adapter_error"),  # what the adapter itself reported (P&I: 'position page did not open for 77 of 77')
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
            f"  exc {e['n_exc']}  {e['seconds']} s" + (f"  ERROR {e['error'].strip().splitlines()[-1][:160]}" if e["error"] else "")
            + (f"  ADAPTER-ERROR {e['adapter_error'][:160]}" if e.get("adapter_error") else ""))


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
        if e["error"] or e["replay_identical"] is False or e["refused"] or e.get("adapter_error"):
            failed.append(e)
    _log(f"done {n} board(s); {len(failed)} with an error, a replay that differs or a refused request")
    for e in failed:
        why = (e["error"].strip().splitlines()[-1][:200] if e["error"] else e["replay_diff"] if e["replay_identical"] is False
               else f"refused {e['refused'][:2]}" if e["refused"] else f"adapter error: {e['adapter_error'][:200]}")
        _log(f"  {e['board_id']}: {why}")


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
    for f in sorted(M.infra_dir().glob("*.sqlite.xz")):  # committed in the repo, not in the mirror root
        e = M.Store.load(f.name.removesuffix(".sqlite.xz")).meta("index")
        _log(f"{e['board_id']:56.56} infra  pages {e['pages']:5} raw {e['bytes_raw'] / 1e6:7.1f} MB xz {f.stat().st_size / 1e6:6.2f} MB rows {e['rows']:>5} {e['recorded_at'][:10]}")
    now = datetime.now(timezone.utc)
    rows, tot = [], {"pages": 0, "raw": 0, "file": 0}
    for bid, e in sorted(idx.items(), key=lambda kv: kv[0]):
        age = (now - datetime.fromisoformat(e["recorded_at"])).days
        tot["pages"] += e["pages"]
        tot["raw"] += e["bytes_raw"]
        tot["file"] += e.get("bytes_file", 0)
        gap = [k for k, ok in (e.get("checks") or {}).items() if not ok]
        flags = [f for f, on in (("ERROR", e.get("error")), ("REPLAY-DIFFERS", e.get("replay_identical") is False), ("EXC", e.get("n_exc")),
                                 ("ADAPTER-ERROR", e.get("adapter_error")),
                                 ("GAP:" + ",".join(gap), gap), (f"OLD>{a.older_than}d", age > a.older_than)) if on]
        rows.append(f"{bid:56.56} {e['kind']:6} clin {e['n_clinics']:2} pages {e['pages']:5} raw {e['bytes_raw'] / 1e6:7.1f} MB "
                    f"xz {e.get('bytes_file', 0) / 1e6:6.2f} MB rows {str(e['rows']):>5} age {age:3}d {e['recorded_at'][:10]}  {' '.join(flags)}")
    _log("\n".join(rows))
    _log(f"{len(idx)} boards, {tot['pages']} pages, {tot['raw'] / 1e9:.2f} GB raw bodies, {tot['file'] / 1e6:.1f} MB on disk; "
         f"'OLD' is information only: nothing refreshes a mirror but a person running `record`; "
         f"GAP = a check already red at recording (an explicit xfail in the suite, a finding about the adapter)")


def cmd_list_urls(a):
    for r in M.Store.load(a.board_id).rows():
        if a.scope in (None, r.scope):
            _log(f"{r.seq:6} {r.scope:22.22} {r.via:10} {r.method:5} {r.status or 'EXC':>4} {r.url}")


def cmd_show(a):
    s = M.Store.load(a.board_id)
    hits = [r for r in s.rows() if r.url == a.url or M._norm_url(r.url) == M._norm_url(a.url)]
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
    idx = {"format": M.FORMAT, "boards": {}}
    for f in sorted(M.mirror_dir().glob("*.sqlite.xz")):
        if f.name.startswith(M.INFRA_PREFIX):  # an old copy: the infra snapshots are read from M.infra_dir() whatever is here
            _log(f"{f.name}: an infra snapshot, they live in {M.infra_dir()}; skipped")
            continue
        e = M.Store.load(f.name.removesuffix(".sqlite.xz")).meta("index")
        if not e:
            _log(f"{f.name}: no index entry in its meta (recorded by something else?), skipped")
            continue
        idx["boards"][e["board_id"]] = dict(e, bytes_file=f.stat().st_size)
    M.write_index(idx)
    _log(f"INDEX.json rebuilt from {len(idx['boards'])} board file(s)")


INFRA_BOARD = "infra__registry-read-proxy"


def cmd_record_infra(a):
    """The registry read proxy as tests/test_geo.py reads it (every town, every (city, plz) pair of the posting observations):
    our own database, not a clinic site, so it is the one recording that may talk to it. Read-only, like everything here."""
    from tests import test_geo as TG
    t0 = time.time()
    with M.recording(INFRA_BOARD, allow_infra=True) as rec:
        with M.scope("geo"):
            pairs, towns = TG._fetch_live_city_plz_pairs(), TG._load_towns()
    e = {"board_id": INFRA_BOARD, "kind": "infra", "url": AC.PROXY, "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "recorder_sha": M.git_sha(), "seconds": round(time.time() - t0, 1), **rec.store.stats(), "rows": len(pairs), "towns": len(towns), "error": None}
    rec.store.set_meta("index", e)
    path = rec.save()  # into M.infra_dir() (tests/fixtures/mirror_infra, committed), headers cut to M.INFRA_HEADERS
    _log(f"{INFRA_BOARD}: {len(pairs)} (city, plz) pairs, {len(towns)} towns, {e['pages']} pages, raw {e['bytes_raw'] / 1e6:.1f} MB, xz {path.stat().st_size / 1e6:.2f} MB -> {path}")


# ---------------------------------------------------------------------------------------------
# push / pull: the mirror in a private Bunny Storage Zone (no pull zone, nothing public; the pages hold third-party HR names)
# ---------------------------------------------------------------------------------------------
BUNNY_BASE_URL = "https://storage.bunnycdn.com"
CHUNK = 1 << 20


def bunny_zone(key_env):
    """(url of the zone, access key) from the environment; every missing name is named. BUNNY_MIRROR_BASE_URL is for the tests."""
    missing = [n for n in ("BUNNY_MIRROR_ZONE", key_env) if not os.environ.get(n)]
    if missing:
        sys.exit(f"missing environment variable(s): {', '.join(missing)}")
    base = (os.environ.get("BUNNY_MIRROR_BASE_URL") or BUNNY_BASE_URL).rstrip("/")
    return f"{base}/{os.environ['BUNNY_MIRROR_ZONE']}", os.environ[key_env]


def bunny_call(method, url, key, ok, **kw):
    """One request, no retry, no redirect (the key would follow it). Any status outside `ok` ends the run, with the URL and without the key."""
    try:
        r = requests.request(method, url, headers={"AccessKey": key}, allow_redirects=False, **kw)
    except requests.RequestException as e:
        sys.exit(f"{method} {url}: {type(e).__name__}: {e}")
    if r.status_code not in ok:
        sys.exit(f"{method} {url}: HTTP {r.status_code} {r.reason}: {r.text[:300]}")
    return r


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def cmd_push(a):
    """One tar (INDEX.json + every board *.sqlite.xz, no *.prev, no infra__* snapshots (they are in the repo), no compression: the boards are xz already), then latest.json, last."""
    prefix, key = bunny_zone("BUNNY_MIRROR_RW_KEY")
    root = M.mirror_dir()
    boards = sorted(p for p in root.glob("*.sqlite.xz") if not p.name.startswith(M.INFRA_PREFIX))
    if not (root / "INDEX.json").is_file() or not boards:
        sys.exit(f"nothing to push: {root} needs INDEX.json and at least one *.sqlite.xz")
    repo_sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    name = f"mirror-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{repo_sha}.tar"
    with tempfile.TemporaryDirectory(prefix="mirror-push-") as tmp:
        path = Path(tmp) / name
        with tarfile.open(path, "w") as tar:
            for p in [root / "INDEX.json", *boards]:
                tar.add(p, arcname=p.name)
        size, digest = path.stat().st_size, sha256_of(path)
        with open(path, "rb") as f:  # streamed from disk: requests sends a file object with its Content-Length
            bunny_call("PUT", f"{prefix}/{name}", key, (200, 201), data=f)
    _log(f"uploaded {name}  {size} bytes  sha256 {digest}  ({len(boards)} boards + INDEX.json)")
    latest = {"archive": name, "sha256": digest, "bytes": size, "boards": len(boards),
              "pushed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "repo_sha": repo_sha}
    bunny_call("PUT", f"{prefix}/latest.json", key, (200, 201), data=json.dumps(latest, indent=1).encode())  # last: a failed upload never moves it
    _log(f"uploaded latest.json -> {name}")


def download(url, key, dest):
    """Stream a GET to `dest`. -> (bytes, sha256)."""
    h, n = hashlib.sha256(), 0
    with bunny_call("GET", url, key, (200,), stream=True) as r:
        try:
            with open(dest, "wb") as f:
                for chunk in r.iter_content(CHUNK):
                    f.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
        except requests.RequestException as e:
            sys.exit(f"GET {url}: the download broke after {n} bytes: {type(e).__name__}: {e}")
    return n, h.hexdigest()


def unpack(archive, out):
    """Unpack into the empty directory `out`. Every member is checked before anything is written: INDEX.json and flat *.sqlite.xz files only."""
    try:
        with tarfile.open(archive, "r:") as tar:
            members = tar.getmembers()
            for m in members:
                parts = PurePosixPath(m.name).parts
                if PurePosixPath(m.name).is_absolute() or ".." in parts:
                    sys.exit(f"unsafe path in the archive: {m.name!r} (absolute or ..); nothing was unpacked")
                if not m.isreg() or len(parts) != 1 or not (m.name == "INDEX.json" or (m.name.endswith(".sqlite.xz") and not m.name.startswith(M.INFRA_PREFIX))):
                    sys.exit(f"unexpected member in the archive: {m.name!r} (only INDEX.json and board *.sqlite.xz files are allowed, no infra__ snapshots); nothing was unpacked")
            if "INDEX.json" not in {m.name for m in members}:
                sys.exit("the archive has no INDEX.json; nothing was unpacked")
            for m in members:
                with tar.extractfile(m) as src, open(out / m.name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except (tarfile.TarError, EOFError) as e:
        sys.exit(f"{archive.name} is not a readable tar: {type(e).__name__}: {e}")
    return [m.name for m in members]


def cmd_pull(a):
    """latest.json -> the archive it names -> checked (bytes, sha256) -> unpacked beside the mirror -> files renamed into it. The mirror stays whole until then."""
    prefix, key = bunny_zone("BUNNY_MIRROR_RO_KEY")
    root = M.mirror_dir()
    raw = bunny_call("GET", f"{prefix}/latest.json", key, (200,)).content
    try:
        latest = json.loads(raw)
        name, want_sha, want_bytes = latest["archive"], latest["sha256"], latest["bytes"]
    except (ValueError, KeyError, TypeError):
        sys.exit(f"{prefix}/latest.json does not name an archive with sha256 and bytes: {raw[:300]!r}")
    if not isinstance(name, str) or Path(name).name != name:
        sys.exit(f"{prefix}/latest.json names {name!r}, which is not a plain file name")
    root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".pull-", dir=root))  # same filesystem as the mirror: the final step is renames
    try:
        n, digest = download(f"{prefix}/{name}", key, stage / name)
        if n != want_bytes:
            sys.exit(f"{name}: {n} bytes downloaded, latest.json says {want_bytes}; the mirror is untouched")
        if digest != str(want_sha).lower():
            sys.exit(f"{name}: sha256 {digest} downloaded, latest.json says {want_sha}; the mirror is untouched")
        (stage / "new").mkdir()
        names = unpack(stage / name, stage / "new")
        for member in sorted(names, key=lambda m: m == "INDEX.json"):  # the index last
            os.replace(stage / "new" / member, root / member)
    finally:
        shutil.rmtree(stage)
    _log(f"pulled {name}  {n} bytes  sha256 {digest}  -> {root} ({len(names) - 1} boards + INDEX.json)")


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
    i = sub.add_parser("record-infra", help="the registry read proxy snapshot tests/test_geo.py reads")
    i.set_defaults(fn=cmd_record_infra)
    sub.add_parser("push", help="upload the mirror to the Bunny zone (latest.json last)").set_defaults(fn=cmd_push)
    sub.add_parser("pull", help="download the mirror the zone's latest.json names").set_defaults(fn=cmd_pull)
    a = p.parse_args()
    if a.cmd == "record" and not (a.all or a.targets):
        p.error("record needs board ids/hosts/clinic ids, or --all")
    a.fn(a)


if __name__ == "__main__":
    main()
