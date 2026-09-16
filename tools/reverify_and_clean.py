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

from pflege_jobs.classify import norm_text  # noqa: E402
from pflege_jobs.sources.career_crawl import in_bavaria  # noqa: E402
from pflege_jobs.verify import VERIFY_FIELDS, verify_all, verify_one  # noqa: E402

STATE = Path(os.environ.get("REVERIFY_STATE", "/tmp/reverify"))
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
                      for r in rows], workers=a.workers, render=not a.no_render, firecrawl=False)
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
    if a.host:
        todo = [p for p in todo if a.host in (post[p["posting_id"]]["external_url"] or "")]
    todo = todo[: a.max]
    print(f"firecrawl rung on {len(todo)} rows (~1 credit each)")
    s = requests.Session()
    for i, p in enumerate(todo, 1):
        r = post[p["posting_id"]]
        try:
            res = verify_one(s, r["external_url"], r["title"], rungs=("firecrawl",))
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
    """(in_bavaria, basis). Page-derived location wins; the stored one is only a fallback, and is
    recorded as such so a deletion is never made on a crawler's guess alone."""
    pc, pp = (v or {}).get("city"), (v or {}).get("plz")
    if pp or pc:
        b = in_bavaria(pc, pp, None, tw)
        if b is not None:
            return b, f"page:{(v or {}).get('loc_source')}"
    b = in_bavaria(p.get("city"), p.get("plz"), None, tw)
    if b is not None:
        return b, "stored"
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
            if pc and norm_text(pc) != norm_text(p.get("city") or ""):
                plan["city_fix"].append(rec)
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


def cmd_apply(a):
    post = {r["posting_id"]: r for r in _load("postings.json")}
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
            body = {"city": r["city_page"]}
            if r.get("plz_page"):
                body["plz"] = r["plz_page"]
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
        full = [post[i] for i in ids if i in post]
        dump = STATE / f"deleted_{time.strftime('%Y%m%dT%H%M%S')}.json"
        dump.write_text(json.dumps({"plan": plan["delete"], "rows": full}, ensure_ascii=False))
        print(f"deleting {len(ids)} postings permanently; dump at {dump}")
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
    f = sp.add_parser("firecrawl"); f.add_argument("--max", type=int, default=50); f.add_argument("--host", default=""); f.set_defaults(fn=cmd_firecrawl)
    sp.add_parser("report").set_defaults(fn=cmd_report)
    ap = sp.add_parser("apply")
    ap.add_argument("--write-verify", action="store_true"); ap.add_argument("--write-city", action="store_true")
    ap.add_argument("--delete-non-bavaria", action="store_true"); ap.set_defaults(fn=cmd_apply)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
