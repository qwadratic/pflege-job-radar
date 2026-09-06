"""Purge Arbeitsagentur (30) + aggregator (40) data from Supabase, register source 25, sync clinic status values.
Backs up to backups/ first. Idempotent.
  set -a; . ./.env; set +a; python3 data/purge_retired_sources.py [--dry]"""
import json, os, sys, time
import requests

U = os.environ["SUPABASE_URL"] + "/rest/v1"
K = os.environ["SUPABASE_SECRET_KEY"]
H = {"apikey": K, "Authorization": "Bearer " + K, "Accept-Profile": "pflege_jobs", "Content-Profile": "pflege_jobs"}
BK = "/home/exedev/repo/backups"
os.makedirs(BK, exist_ok=True)
DRY = "--dry" in sys.argv


def count(rel, flt=""):
    r = requests.head(f"{U}/{rel}?select=*&{flt}&limit=1", headers={**H, "Prefer": "count=exact"}, timeout=120)
    cr = r.headers.get("content-range", "*/?")
    return int(cr.split("/")[1]) if cr.split("/")[1].isdigit() else cr


def page(rel, select, flt="", order="", step=1000):
    out, off = [], 0
    while True:
        r = requests.get(f"{U}/{rel}?select={select}&{flt}&order={order}&limit={step}&offset={off}", headers=H, timeout=180)
        r.raise_for_status()
        rows = r.json()
        out.extend(rows)
        if len(rows) < step:
            return out
        off += step


def delete(rel, flt):
    if DRY:
        print("  DRY delete", rel, flt[:80]); return 0
    r = requests.delete(f"{U}/{rel}?{flt}", headers={**H, "Prefer": "return=minimal,count=exact"}, timeout=300)
    if r.status_code >= 300:
        print("  ERR", r.status_code, r.text[:200]); raise SystemExit(1)
    cr = r.headers.get("content-range", "*/?")
    return int(cr.split("/")[1]) if cr.split("/")[1].isdigit() else -1


def chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def report(tag):
    print(f"[{tag}] postings={count('postings')} open={count('postings','status=eq.open')} employers={count('employers')} "
          f"obs20={count('posting_observations','source_id=eq.20')} obs25={count('posting_observations','source_id=eq.25')} "
          f"obs30={count('posting_observations','source_id=eq.30')} obs40={count('posting_observations','source_id=eq.40')} "
          f"inbox={count('inbox')} crawl_runs={count('crawl_runs')}")


report("before")

# a. source 25
r = requests.post(f"{U}/sources", headers={**H, "Prefer": "resolution=merge-duplicates,return=minimal"}, timeout=60,
                  json=[{"source_id": 25, "code": "firecrawl_agent", "name": "Firecrawl agent (career site, LLM-extracted)",
                         "kind": "employer_ats", "precedence": 2, "base_url": "https://api.firecrawl.dev/v2/agent"}])
print("source 25:", r.status_code, r.text[:120])

# b. backup + delete observations 30/40
obs = page("posting_observations", "*", "source_id=in.(30,40)", "observation_id")
print("obs to purge:", len(obs))
if obs and not DRY:
    with open(f"{BK}/observations_30_40_{time.strftime('%Y%m%dT%H%M%S')}.jsonl", "w", encoding="utf-8") as f:
        for o in obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
ids = [o["observation_id"] for o in obs]
n = 0
for c in chunks(ids, 300):
    n += delete("posting_observations", "observation_id=in.(" + ",".join(map(str, c)) + ")")
print("deleted observations:", n)

# c. orphan postings
all_p = {x["posting_id"] for x in page("postings", "posting_id", "", "posting_id")}
with_obs = {x["posting_id"] for x in page("posting_observations", "posting_id", "posting_id=not.is.null", "observation_id") }
orphans = sorted(all_p - with_obs)
print("orphan postings:", len(orphans))
if orphans and not DRY:
    rows = []
    for c in chunks(orphans, 500):
        rows += page("postings", "*", "posting_id=in.(" + ",".join(map(str, c)) + ")", "posting_id")
    with open(f"{BK}/postings_orphaned_{time.strftime('%Y%m%dT%H%M%S')}.jsonl", "w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
n = 0
for c in chunks(orphans, 300):
    n += delete("postings", "posting_id=in.(" + ",".join(map(str, c)) + ")")
print("deleted postings:", n)

# c2. inbox rows from indeed/stepstone/egress collectors
r = requests.get(f"{U}/inbox?select=*&limit=1", headers=H, timeout=60)
cols = list(r.json()[0].keys()) if r.ok and r.json() else []
print("inbox cols:", cols)
if "collector" in cols:
    flt = "or=(collector.ilike.*egress*,collector.ilike.*indeed*,collector.ilike.*stepstone*,source_host.ilike.*indeed.com*,source_host.ilike.*stepstone.de*)"
    print("inbox to purge:", count("inbox", flt))
    print("deleted inbox:", delete("inbox", flt))

# d. resolve
if not DRY:
    r = requests.post(f"{U}/rpc/resolve_postings", headers=H, json={}, timeout=600)
    print("resolve_postings:", r.status_code, r.text[:200])
    if r.status_code >= 300:
        time.sleep(5)
        r = requests.post(f"{U}/rpc/resolve_postings", headers=H, json={}, timeout=600)
        print("resolve_postings retry:", r.status_code, r.text[:200])

# e. orphan employers
emp = {x["employer_id"] for x in page("employers", "employer_id", "", "employer_id")}
used = {x["employer_id"] for x in page("postings", "employer_id", "employer_id=not.is.null", "posting_id")}
used |= {x["employer_id"] for x in page("posting_observations", "employer_id", "employer_id=not.is.null", "observation_id")}
used |= {x["employer_id"] for x in page("clinics", "employer_id", "employer_id=not.is.null", "clinic_id")}
dead = sorted(emp - used)
print("orphan employers:", len(dead))
n = 0
for c in chunks(dead, 300):
    n += delete("employers", "employer_id=in.(" + ",".join(map(str, c)) + ")")
print("deleted employers:", n)

# f. crawl_runs FK -> null, then sources 30/40
if not DRY:
    r = requests.patch(f"{U}/crawl_runs?source_id=in.(30,40)", headers={**H, "Prefer": "return=minimal"}, json={"source_id": None}, timeout=120)
    print("crawl_runs unlinked:", r.status_code, r.text[:100])
print("deleted sources:", delete("sources", "source_id=in.(30,40)"))

# g. clinic status values: CSV (canonical, cleaned by krankenhausplan._status) -> DB
import csv
csv_status = {r["clinic_id"]: r["status"] for r in csv.DictReader(open("/home/exedev/repo/data/registry/clinics.csv", encoding="utf-8"))}
db_status = {x["clinic_id"]: x["status"] for x in page("clinics", "clinic_id,status", "", "clinic_id")}
diff = [(cid, db_status.get(cid), st) for cid, st in csv_status.items() if cid in db_status and db_status[cid] != st]
print("clinic status fixes:", len(diff))
for cid, old, new in diff:
    if DRY:
        print("  DRY", cid, old, "->", new); continue
    r = requests.patch(f"{U}/clinics?clinic_id=eq.{cid}", headers={**H, "Prefer": "return=minimal"}, json={"status": new}, timeout=60)
    if r.status_code >= 300:
        print("  ERR", cid, r.status_code, r.text[:100])

report("after")
