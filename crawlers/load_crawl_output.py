"""Load Playwright-crawler output (crawl_output/*.jsonl, one inbox-shaped row per line: kind, source_host, source_url, payload,
collector, client_id) into pflege_jobs.inbox, run the inbox processor + dedupe, then print what changed.

  python crawlers/load_crawl_output.py [crawl_output/]           # needs SUPABASE_URL, SUPABASE_ANON_KEY, PFLEGE_INGEST_*
"""
import glob, json, os, subprocess, sys, time
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

U = os.environ["SUPABASE_URL"]; H = {"apikey": os.environ["SUPABASE_ANON_KEY"], "Accept-Profile": "pflege_jobs"}
d = sys.argv[1] if len(sys.argv) > 1 else "crawl_output"


def q(path):
    return requests.get(f"{U}/rest/v1/{path}", headers=H, timeout=120).json()


def snapshot():
    v = q("v_postings?select=posting_id&employer_class=eq.clinic&is_pflege=eq.true&status=eq.open&verify_status=eq.live&limit=100000")
    # v_postings?source_codes=cs.{aggregator} times out (per-row subquery over 6k+ postings) on the anon
    # role's statement_timeout; posting_observations filtered by source_id is indexed and fast.
    agg = q("posting_observations?select=posting_id&source_id=eq.40&posting_id=not.is.null&limit=100000")
    ats = q("clinics?select=clinic_id,ats_type&ats_type=not.is.null&limit=1000")
    return {"live": {x["posting_id"] for x in v}, "agg": {x["posting_id"] for x in agg}, "ats": {x["clinic_id"]: x["ats_type"] for x in ats}}


rows = []
for f in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except Exception: continue
        if r.get("kind") in ("jobposting", "listing", "probe") and r.get("source_url"): rows.append(r)
seen = set(); rows = [r for r in rows if not (r["source_url"] in seen or seen.add(r["source_url"]))]
print(f"{len(rows)} rows from {d}/*.jsonl")
before = snapshot()
for i in range(0, len(rows), 200):
    r = requests.post(f"{U}/rest/v1/inbox", headers={**H, "Authorization": "Bearer " + os.environ["SUPABASE_ANON_KEY"], "Content-Profile": "pflege_jobs", "Content-Type": "application/json", "Prefer": "return=minimal"},
                      json=rows[i:i + 200], timeout=120)
    r.raise_for_status()
print("posted to inbox"); time.sleep(2)
subprocess.run([sys.executable, "-m", "pflege_jobs.cli", "inbox"], check=False)
subprocess.run([sys.executable, "-m", "pflege_jobs.cli", "link-cross"], check=False)
time.sleep(2); after = snapshot()
new_live = after["live"] - before["live"]; new_agg = after["agg"] - before["agg"]
print("\n=== RESULT ===")
print(f"new verified clinic Pflege postings: {len(new_live)}  (total now {len(after['live'])})")
print(f"new aggregator-sourced postings:     {len(new_agg)}")
changed = {c: (before["ats"].get(c), t) for c, t in after["ats"].items() if before["ats"].get(c) != t}
print(f"ATS labels set/changed:              {len(changed)}")
if changed:
    names = {x["clinic_id"]: x["name"] for x in q("clinics?select=clinic_id,name&clinic_id=in.(" + ",".join(changed) + ")")}
    for c, (o, n) in changed.items(): print(f"   {names.get(c, c)}: {o or '—'} -> {n}")
notes = q("inbox?select=process_note&collector=like.*egress*&order=inbox_id.desc&limit=2000")
from collections import Counter
print("inbox notes:", Counter((n["process_note"] or "").split(" ->")[0].split(" (")[0] for n in notes).most_common(8))
