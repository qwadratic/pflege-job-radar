"""Load Playwright-crawler output (crawl_output/*.jsonl, one inbox-shaped row per line: kind, source_host, source_url, payload,
collector, client_id) into the local raw queue (pflege_jobs.inbox_db), run the inbox processor + dedupe, then print what changed.

  python crawlers/load_crawl_output.py [crawl_output/]           # needs SUPABASE_URL, SUPABASE_ANON_KEY, PFLEGE_INGEST_*
"""
import glob, json, os, subprocess, sys, time
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

U = os.environ["SUPABASE_URL"]; H = {"apikey": os.environ["SUPABASE_ANON_KEY"], "Accept-Profile": "pflege_jobs"}
d = sys.argv[1] if len(sys.argv) > 1 else "crawl_output"


def q(path):
    r = requests.get(f"{U}/rest/v1/{path}", headers=H, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"GET {path} -> HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def snapshot():
    # TASK-154: v_postings' employer_class column is a per-row CASE expression that (to resolve its
    # 'unknown'-downgrade branch) depends on a linked_towns CTE -- a GroupAggregate over every postings
    # row with clinic_id set, recomputed on every query that filters on employer_class (confirmed live
    # via EXPLAIN ANALYZE: filtering v_postings on employer_class=eq.clinic cost 8675 buffers/~965ms;
    # the same result set filtered on the plain postings.clinic_id instead cost 1566 buffers/~6ms --
    # postgres only prunes that CTE when employer_class isn't referenced at all, per TASK-124). Reading
    # the base postings table (clinic_id is not null, joined to role_classes for is_pflege) instead of
    # v_postings avoids that CTE entirely, the same "read the base tables" pattern cmd_link_clinics
    # already uses for the same reason. Trade-off, measured live: this counts postings.clinic_id IS NOT
    # NULL rather than the view's employer_class='clinic' (which also counts a small number of postings
    # whose EMPLOYER is clinic-classified but this specific posting has no clinic_id yet) -- 2488 vs
    # 2569 rows live on 2026-09-24, ~3% fewer. Acceptable here: snapshot() only powers this script's own
    # before/after diagnostic counts, not a data write.
    v = q("postings?select=posting_id,role_classes!inner(is_pflege)"
          "&clinic_id=not.is.null&status=eq.open&verify_status=eq.live&role_classes.is_pflege=eq.true&limit=100000")
    ats = q("clinics?select=clinic_id,ats_type&ats_type=not.is.null&limit=1000")
    return {"live": {x["posting_id"] for x in v}, "ats": {x["clinic_id"]: x["ats_type"] for x in ats}}


rows = []
for f in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except Exception: continue
        if r.get("kind") in ("jobposting", "listing", "probe", "observation") and r.get("source_url"): rows.append(r)
seen = set(); rows = [r for r in rows if not (r["source_url"] in seen or seen.add(r["source_url"]))]
print(f"{len(rows)} rows from {d}/*.jsonl")
before = snapshot()
# The local queue, not pflege_jobs.inbox: a directory of crawl output is thousands of rows and the
# Postgres table takes 2000 per client per rolling 24h (TASK-95). `cli inbox` drains both.
from pflege_jobs import inbox_db as IB
print(f"queued {IB.enqueue(rows)} rows in {IB.PATH}"); time.sleep(2)
subprocess.run([sys.executable, "-m", "pflege_jobs.cli", "inbox"], check=False)
subprocess.run([sys.executable, "-m", "pflege_jobs.cli", "link-cross"], check=False)
time.sleep(2); after = snapshot()
new_live = after["live"] - before["live"]
print("\n=== RESULT ===")
print(f"new verified clinic Pflege postings: {len(new_live)}  (total now {len(after['live'])})")
changed = {c: (before["ats"].get(c), t) for c, t in after["ats"].items() if before["ats"].get(c) != t}
print(f"ATS labels set/changed:              {len(changed)}")
if changed:
    names = {x["clinic_id"]: x["name"] for x in q("clinics?select=clinic_id,name&clinic_id=in.(" + ",".join(changed) + ")")}
    for c, (o, n) in changed.items(): print(f"   {names.get(c, c)}: {o or '—'} -> {n}")
notes = IB.connect().execute("select process_note from inbox order by inbox_id desc limit 2000")
from collections import Counter
print("inbox notes:", Counter((n["process_note"] or "").split(" ->")[0].split(" (")[0] for n in notes).most_common(8))
