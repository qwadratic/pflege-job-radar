"""Re-verify every posting against its own page, correct the city from that page, drop the non-Bavarian ones.

Ivan, 2026-09-16: "status must be usable as the only filter" -- which needs three things this script does
in one pass, because they all depend on the same page fetch:

  1. a status that came from a REAL request (plain HTTP -> Playwright -> Firecrawl, see pflege_jobs/verify.py).
     Statuses asserted by a crawler ("collected in a user's browser", "crawled live from career site",
     ~1690 rows) are not evidence and are re-checked here like everything else;
  2. the city the POSTING'S OWN PAGE states -- the stored one is whatever the crawler had, and a crawler
     with no per-posting location falls back to the seed clinic's town (TASK-59a: Oberhausen postings
     stored as "Neuburg/Donau"), so the Bavaria decision cannot be made from the stored value;
  3. permanent deletion of everything that is not in Bavaria once (2) says so.

Phases are separate commands so a long run can be resumed and so nothing destructive happens by accident:

    python tools/reverify_and_clean.py fetch                   # pull postings -> STATE/postings.json
    python tools/reverify_and_clean.py verify [--limit N] [--workers N] [--no-render]
    python tools/reverify_and_clean.py firecrawl [--max N]     # only the rows HTTP+browser could not see
    python tools/reverify_and_clean.py report                  # what would change; writes STATE/plan.json
    python tools/reverify_and_clean.py apply --write-verify    # push verify verdicts (+expire 'gone')
    python tools/reverify_and_clean.py apply --write-city      # push page-derived city/plz/in_bavaria
    python tools/reverify_and_clean.py apply --delete-non-bavaria   # PERMANENT delete, dumps rows first
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pflege_jobs.classify import employer_norm, norm_text  # noqa: E402
from pflege_jobs.sources.career_crawl import in_bavaria  # noqa: E402
from pflege_jobs.verify import TRUSTED_LOC, VERIFY_FIELDS, _placeable, verify_all, verify_one  # noqa: E402

STATE = Path(os.environ.get("REVERIFY_STATE", "/tmp/reverify"))
BACKUPS = Path(__file__).resolve().parent.parent / "backups"  # durable -- /tmp/reverify (STATE's default) is tmpfs, a reboot erases it
SUPA = os.environ["SUPABASE_URL"]
ANON = os.environ["SUPABASE_ANON_KEY"]
SECRET = os.environ.get("SUPABASE_SECRET_KEY")
DIRECT = "https://klkxfvieaxpjlplloljn.supabase.co"          # writes bypass the read proxy
COLS = ("posting_id,title,status,verify_status,verify_http,verified_at,verify_note,city,plz,in_bavaria,"
        "clinic_id,clinic_match_rule,external_url,first_seen,last_seen")


def _h(write=False):
    key = SECRET if write else ANON
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Accept-Profile": "pflege_jobs"}
    if write:
        h["Content-Profile"] = "pflege_jobs"
    return h


def _load(name):
    return json.loads((STATE / name).read_text())


def _save(name, obj):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(json.dumps(obj, ensure_ascii=False))
    print(f"  -> {STATE / name} ({len(obj)} rows)" if isinstance(obj, list) else f"  -> {STATE / name}")


def towns():
    import csv
    p = Path(__file__).resolve().parent.parent / "data" / "registry" / "clinics.csv"
    return {norm_text(r["town"]) for r in csv.DictReader(p.open(encoding="utf-8")) if r.get("town")}


# --- phases ---------------------------------------------------------------------------------------
def cmd_fetch(a):
    rows, off = [], 0
    while True:
        r = requests.get(f"{SUPA}/rest/v1/postings", params={"select": COLS, "order": "posting_id", "limit": 1000, "offset": off},
                         headers=_h(), timeout=120)
        ch = r.json()
        if not isinstance(ch, list):
            raise SystemExit(f"fetch failed: {ch}")
        rows += ch
        off += len(ch)
        if len(ch) < 1000:
            break
    print(f"fetched {len(rows)} postings")
    _save("postings.json", rows)


def cmd_verify(a):
    rows = _load("postings.json")
    if a.limit:
        rows = rows[: a.limit]
    done = {}
    if (STATE / "verified.json").exists() and not a.restart:
        done = {r["posting_id"]: r for r in _load("verified.json")}
        rows = [r for r in rows if r["posting_id"] not in done]
        print(f"resuming: {len(done)} already verified, {len(rows)} left")
    t0 = time.time()
    res = verify_all([{"posting_id": r["posting_id"], "external_url": r["external_url"], "source_url": None, "title": r["title"]}
                      for r in rows], workers=a.workers, render=not a.no_render, firecrawl=False, towns=towns())
    for x in res:
        done[x["posting_id"]] = x
    out = list(done.values())
    print(f"verified {len(res)} in {time.time() - t0:.0f}s; total {len(out)}")
    print("  status:", dict(Counter(x["verify_status"] for x in out)))
    print("  method:", dict(Counter(x["method"] for x in out)))
    _save("verified.json", out)


def cmd_firecrawl(a):
    """Last rung, for rows neither HTTP nor a browser could see. One Firecrawl credit per row."""
    post = {r["posting_id"]: r for r in _load("postings.json")}
    done = {r["posting_id"]: r for r in _load("verified.json")}
    todo = [p for p in done.values() if p["verify_status"] in ("blocked", "error")]
    # verified.json can outlive the postings.json it was built from (a delete/refetch generation
    # in between) -- indexing `post` below on every id in `todo` used to raise KeyError mid-run,
    # discarding every Firecrawl verdict already paid for in that call (documented 2026-09-16: died
    # at row 41/50 after 40 credits spent). Drop stale ids here, once, so nothing after this line
    # ever indexes `post` on an id it might not have.
    stale = [p for p in todo if p["posting_id"] not in post]
    if stale:
        todo = [p for p in todo if p["posting_id"] in post]
        print(f"  dropping {len(stale)} stale id(s) not in postings.json: {[p['posting_id'] for p in stale][:10]}")
    if a.host:
        todo = [p for p in todo if a.host in (post[p["posting_id"]]["external_url"] or "")]
    if a.keep_only and (STATE / "plan.json").exists():
        # a credit spent on a posting that is about to be deleted as non-Bavarian buys nothing:
        # 430 of the 558 walled rows are already non-Bavarian by their stored city
        plan = _load("plan.json")
        wanted = {r["posting_id"] for r in plan["keep"]} | {r["posting_id"] for r in plan["unknown_bavaria"]}
        todo = [p for p in todo if p["posting_id"] in wanted]
    todo = todo[: a.max]
    print(f"firecrawl rung on {len(todo)} rows (~1 credit each)")
    s = requests.Session()
    tw = towns()
    for i, p in enumerate(todo, 1):
        r = post[p["posting_id"]]
        try:
            res = verify_one(s, r["external_url"], r["title"], rungs=("firecrawl",), towns=tw)
        except Exception as e:
            res = {**p, "verify_note": f"{p.get('verify_note')}; firecrawl crashed {type(e).__name__}"}
        if res.get("verify_status") in ("live", "gone"):
            done[r["posting_id"]] = {**p, **{k: res[k] for k in ("verify_status", "verify_http", "verify_note", "method", "city", "plz", "loc_source")}}
        if i % 25 == 0:
            print(f"  {i}/{len(todo)}")
            _save("verified.json", list(done.values()))
    _save("verified.json", list(done.values()))
    print("  status now:", dict(Counter(x["verify_status"] for x in done.values())))


def _decide_bavaria(p, v, tw):
    """(in_bavaria, basis). Deletion is permanent, so only evidence that actually STATES the job's
    location may decide it:
      * page:jsonld / page:einsatzort -- the page says where the job is. Decides on its own.
      * stored -- the crawler's value. Decides on its own for the obvious non-Bavarian cities
        (Berlin, Hamburg, ...), because no Bavaria-only registry seed can produce those by accident.
      * page:plz_ort -- the first '12345 Ort' anywhere on the page. NOT a statement about the job:
        it is regularly the operator's head office (Viechtach postings carry Ulm's 89077 in the
        footer, 2026-09-16). Used only to CONFIRM a stored non-Bavarian verdict, never to create one.
    Anything else stays unknown and is kept."""
    src = (v or {}).get("loc_source")
    pc, pp = (v or {}).get("city"), (v or {}).get("plz")
    if src in TRUSTED_LOC and (pc or pp):
        b = in_bavaria(pc, pp, None, tw)
        if b is not None:
            return b, f"page:{src}"
    stored = in_bavaria(p.get("city"), p.get("plz"), None, tw)
    if stored is False and src == "plz_ort" and in_bavaria(pc, pp, None, tw) is False:
        return False, "stored+page:plz_ort"
    if stored is not None:
        return stored, "stored"
    if p.get("in_bavaria") is False:
        # last fallback: the flag the intake pipeline computed, which saw fields (addressRegion) that
        # are no longer on the row. Recomputing from city/plz alone left 11 Henstedt-Ulzburg /
        # Bad Elster / Langenhagen rows undecidable although the flag had them right.
        return False, "stored_flag"
    return None, "unknown"


def cmd_report(a):
    post = {r["posting_id"]: r for r in _load("postings.json")}
    ver = {r["posting_id"]: r for r in _load("verified.json")} if (STATE / "verified.json").exists() else {}
    tw = towns()
    plan = {"delete": [], "keep": [], "city_fix": [], "unknown_bavaria": [], "unverifiable": []}
    host_fail = defaultdict(lambda: {"n": 0, "notes": Counter()})
    for pid, p in post.items():
        v = ver.get(pid)
        bav, basis = _decide_bavaria(p, v, tw)
        rec = {"posting_id": pid, "city_stored": p.get("city"), "city_page": (v or {}).get("city"),
               "plz_page": (v or {}).get("plz"), "basis": basis, "status": (v or {}).get("verify_status"),
               "method": (v or {}).get("method"), "url": p.get("external_url"), "clinic_id": p.get("clinic_id")}
        if v and v["verify_status"] in ("blocked", "error"):
            plan["unverifiable"].append(rec)
            h = urlparse(p.get("external_url") or "").netloc
            host_fail[h]["n"] += 1
            host_fail[h]["notes"][str(v.get("verify_note"))[:60]] += 1
        if bav is False:
            plan["delete"].append(rec)
        elif bav is None:
            plan["unknown_bavaria"].append(rec)
        else:
            plan["keep"].append(rec)
            pc = (v or {}).get("city")
            if pc and (v or {}).get("loc_source") in TRUSTED_LOC and norm_text(pc) != norm_text(p.get("city") or ""):
                plan["city_fix"].append(rec)      # only a stated location may overwrite a stored city
    print(f"postings           : {len(post)}")
    print(f"  verified this run: {len(ver)}")
    print(f"  keep (Bavaria)   : {len(plan['keep'])}")
    print(f"  DELETE (non-BY)  : {len(plan['delete'])}")
    print(f"  bavaria unknown  : {len(plan['unknown_bavaria'])}")
    print(f"  city needs fixing: {len(plan['city_fix'])}")
    print(f"  unverifiable     : {len(plan['unverifiable'])}")
    print("\nhosts nothing could verify:")
    for h, d in sorted(host_fail.items(), key=lambda kv: -kv[1]["n"])[:15]:
        print(f"  {d['n']:5d}  {h:45s} {d['notes'].most_common(1)[0][0]}")
    _save("plan.json", plan)


def cmd_relink(a):
    """Re-run the registry Matcher with the city the PAGE stated, and report every posting whose
    stored clinic_id disagrees. A clinic link derived from a wrong city is wrong by construction --
    same failure as TASK-59a, one layer down.

    Decisions are made BY Matcher.match itself (board=[old_clinic_id], employer_inherited), the same
    ladder app/crawl.py uses at crawl time, not by a bespoke city-string comparison:
      * board=[old] lets R0_board re-confirm the EXISTING link on its own canonical town rule
        (_town_match) -- a disagreement is only ever the ladder's own verdict.
      * employer_inherited is set when the posting's employer text is just an echo of the clinic it
        is already linked to (R1/R2 cannot tell that from a real employer-name match, see
        registry.Matcher.match's docstring) so that echo cannot rubber-stamp a link the page's own
        city contradicts -- this is exactly the group-board shape (kbo.de's every posting carrying
        the operator HQ address) that nulled 13 of 29 CORRECT links on the 2026-09-16 22:24 run.
    A disagreement clears the link only when the registry has NO clinic anywhere in that town;
    otherwise a real candidate exists that the ladder just couldn't pick with confidence, and it is
    reported but the existing link is left alone rather than destroyed on a guess (11 of the 29
    postings that run wrongly unlinked fall in exactly this bucket).

    Only a TRUSTED_LOC city that is itself placeable (in_bavaria() reaches a verdict on it) counts as
    evidence -- 2026-09-16: the untrusted plz_ort fallback moved a Noerdlingen posting to Donauwoerth
    off an Ulm footer address, and before AC#1's towns= fix an unplaceable label ("Karte") could pass
    as a page city here too."""
    from pflege_jobs.registry import Matcher, _town_match, city_key
    post = {r["posting_id"]: r for r in _load("postings.json")}
    ver = {r["posting_id"]: r for r in _load("verified.json")}
    plan = _load("plan.json")
    tw = towns()
    keep = {r["posting_id"] for r in plan["keep"]}
    emp = {}
    ids = [str(i) for i in keep]
    for i in range(0, len(ids), 200):
        q = ",".join(ids[i:i + 200])
        for x in requests.get(f"{SUPA}/rest/v1/v_postings", params={"select": "posting_id,employer,clinic_id,city", "posting_id": f"in.({q})"},
                              headers=_h(), timeout=120).json():
            emp[x["posting_id"]] = x
    clinics = requests.get(f"{SUPA}/rest/v1/clinics", params={"select": "clinic_id,name,town,operator,beds"}, headers=_h(), timeout=120).json()
    m = Matcher([dict(c) for c in clinics])
    by_town = {c["clinic_id"]: c.get("town") for c in clinics}
    changes, cleared, disagreements = [], [], []
    for pid in keep:
        v, e = ver.get(pid), emp.get(pid)
        if not e:
            continue
        page_city, page_plz = ((v or {}).get("city"), (v or {}).get("plz")) if (v or {}).get("loc_source") in TRUSTED_LOC else (None, None)
        if not page_city or not _placeable(page_city, page_plz, tw):
            continue                                   # no stated, placeable location -> no evidence to relink on
        old = e.get("clinic_id")
        old_clinic = m.by_id.get(str(old)) if old else None
        en = employer_norm(e.get("employer") or "")
        # The employer text is circular evidence, not independent confirmation, when it is just the
        # OLD clinic's own name/operator copied onto the posting (registry.Matcher.match's docstring)
        # -- R1/R2 would then "confirm" old on every posting of a shared board regardless of what the
        # page itself says, which is the exact kbo.de shape this fix exists for.
        inherited = bool(en) and bool(old_clinic) and en in (employer_norm(old_clinic.get("name") or ""), employer_norm(old_clinic.get("operator") or ""))
        res = m.match(e.get("employer"), page_city, board=[old] if old else None, employer_inherited=inherited)
        new = res[0] if res else None
        if new and new != old and _town_match(city_key(by_town.get(new) or ""), city_key(page_city)):
            changes.append({"posting_id": pid, "old": old, "new": new, "rule": res[1], "city": page_city,
                            "old_town": by_town.get(old), "new_town": by_town.get(new)})
        elif not new and old:
            rec = {"posting_id": pid, "old": old, "city": page_city, "old_town": by_town.get(old)}
            if m._by_town(city_key(page_city)):
                disagreements.append(rec)              # a clinic exists in that town; the ladder just could not pick it -- leave old linked
            else:
                cleared.append(rec)                    # no clinic anywhere in that town -- safe to unlink
    print(f"relink: {len(changes)} posting(s) point at the wrong clinic, {len(cleared)} unlinked (no clinic in the page's town), "
          f"{len(disagreements)} left linked despite disagreeing (a clinic exists in that town but the ladder could not pick it -- needs a human)")
    for c in changes[:15]:
        print(f"  {c['posting_id']:6d} {c['old']}({c['old_town']}) -> {c['new']}({c['new_town']}) via {c['rule']} | page city {c['city']!r}")
    for c in disagreements[:15]:
        print(f"  DISAGREE {c['posting_id']:6d} {c['old']}({c['old_town']}) vs page city {c['city']!r} -- left linked")
    _save("relink.json", {"changes": changes, "cleared": cleared, "disagreements": disagreements})
    if a.write:
        from pflege_jobs.sinks import EdgeSink
        sink = EdgeSink(batch=400)
        links = [{"posting_id": c["posting_id"], "clinic_id": c["new"], "clinic_match_rule": c["rule"] + "|page_city", "clinic_match_score": 0.9} for c in changes]
        links += [{"posting_id": c["posting_id"], "clinic_id": None, "clinic_match_rule": None, "clinic_match_score": None} for c in cleared]
        n = 0
        for i in range(0, len(links), 400):
            n += sink._post({"clinic_links": links[i:i + 400]}).get("clinic_links", 0)
        print(f"  wrote {n} link(s)")


def cmd_apply(a):
    plan = _load("plan.json")
    ver = {r["posting_id"]: r for r in _load("verified.json")}

    if a.write_verify:
        rows = [{k: v for k, v in r.items() if k in VERIFY_FIELDS} for r in ver.values()]
        from pflege_jobs.sinks import EdgeSink
        sink, n = EdgeSink(batch=400), 0
        for i in range(0, len(rows), 400):
            n += sink._post({"verify": rows[i:i + 400]}).get("verify", 0)
        print(f"verify pushed: {n}")

    if a.write_city:
        fixes = [r for r in plan["city_fix"] if r.get("city_page")]
        print(f"city fixes: {len(fixes)}")
        for i, r in enumerate(fixes, 1):
            # city/plz alone do not survive the next crawl: resolve_postings() recomputes both of
            # them from posting_observations on every run with nothing to check first (sql/001_schema
            # .sql), so a plain PATCH here was silently undone by the next ingest (TASK-74 AC3,
            # confirmed live 2026-09-16). *_override sits outside that recompute -- only this tool
            # ever writes it -- so it is what actually makes the correction durable; city/plz are
            # still set too so the fix is visible immediately, not just after the next crawl.
            body = {"city": r["city_page"], "city_override": r["city_page"]}
            if r.get("plz_page"):
                body["plz"] = r["plz_page"]
                body["plz_override"] = r["plz_page"]
            resp = requests.patch(f"{DIRECT}/rest/v1/postings", params={"posting_id": f"eq.{r['posting_id']}"},
                                  headers=_h(write=True), json=body, timeout=30)
            if resp.status_code >= 300:
                print(f"  {r['posting_id']} FAILED {resp.status_code} {resp.text[:120]}")
            if i % 100 == 0:
                print(f"  {i}/{len(fixes)}")

    if a.delete_non_bavaria:
        ids = [r["posting_id"] for r in plan["delete"]]
        if not ids:
            print("nothing to delete")
            return
        # Full rows (select=*), for BOTH tables, dumped BEFORE anything is deleted -- same pattern as
        # data/purge_retired_sources.py. The old dump used postings.json's own 15-column COLS slice
        # and never touched posting_observations at all: 2026-09-16's run deleted 973 postings + all
        # of their observations with only a partial backup for 30 of them and none of the
        # observations anywhere (TASK-74 AC4). BACKUPS (not STATE) because /tmp/reverify is tmpfs --
        # the same durable location data/purge_retired_sources.py and data/backup_postings.py use.
        bk = BACKUPS
        bk.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%S")
        full_posts, full_obs = [], []
        for i in range(0, len(ids), 100):
            q = ",".join(str(x) for x in ids[i:i + 100])
            ps = requests.get(f"{SUPA}/rest/v1/postings", params={"select": "*", "posting_id": f"in.({q})"}, headers=_h(), timeout=120).json()
            os_ = requests.get(f"{SUPA}/rest/v1/posting_observations", params={"select": "*", "posting_id": f"in.({q})"}, headers=_h(), timeout=120).json()
            if not isinstance(ps, list) or not isinstance(os_, list):
                raise SystemExit(f"backup fetch failed, deleting nothing: postings={ps!r} observations={os_!r}")
            full_posts += ps
            full_obs += os_
        dump_posts, dump_obs = bk / f"reverify_delete_postings_{ts}.jsonl", bk / f"reverify_delete_observations_{ts}.jsonl"
        dump_posts.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in full_posts))
        dump_obs.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in full_obs))
        if len(full_posts) < len(ids):
            # Loud, not absorbed into the count below: a posting missing from the backup fetch (already
            # gone, or a transient read-proxy gap) must not be silently deleted with no recovery copy.
            print(f"  WARNING: backed up {len(full_posts)}/{len(ids)} postings rows -- {len(ids) - len(full_posts)} would delete with NO backup")
        print(f"deleting {len(ids)} postings permanently; backup: {len(full_posts)} posting row(s) -> {dump_posts}, "
              f"{len(full_obs)} observation row(s) -> {dump_obs}")
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            q = ",".join(str(x) for x in chunk)
            r1 = requests.delete(f"{DIRECT}/rest/v1/posting_observations", params={"posting_id": f"in.({q})"}, headers=_h(write=True), timeout=60)
            r2 = requests.delete(f"{DIRECT}/rest/v1/postings", params={"posting_id": f"in.({q})"}, headers=_h(write=True), timeout=60)
            if r1.status_code >= 300 or r2.status_code >= 300:
                print(f"  chunk {i}: obs {r1.status_code} {r1.text[:80]} | post {r2.status_code} {r2.text[:80]}")
            if (i // 100) % 5 == 0:
                print(f"  {min(i + 100, len(ids))}/{len(ids)}")
        print("deleted")


def main(argv=None):
    p = argparse.ArgumentParser(prog="reverify_and_clean")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("fetch").set_defaults(fn=cmd_fetch)
    v = sp.add_parser("verify"); v.add_argument("--limit", type=int, default=0); v.add_argument("--workers", type=int, default=10)
    v.add_argument("--no-render", action="store_true"); v.add_argument("--restart", action="store_true"); v.set_defaults(fn=cmd_verify)
    f = sp.add_parser("firecrawl"); f.add_argument("--max", type=int, default=50); f.add_argument("--host", default=""); f.add_argument("--keep-only", action="store_true"); f.set_defaults(fn=cmd_firecrawl)
    sp.add_parser("report").set_defaults(fn=cmd_report)
    rl = sp.add_parser("relink"); rl.add_argument("--write", action="store_true"); rl.set_defaults(fn=cmd_relink)
    ap = sp.add_parser("apply")
    ap.add_argument("--write-verify", action="store_true"); ap.add_argument("--write-city", action="store_true")
    ap.add_argument("--delete-non-bavaria", action="store_true"); ap.set_defaults(fn=cmd_apply)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
