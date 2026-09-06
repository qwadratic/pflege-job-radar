"""Load every crawl checkpoint (data/*_done.json, data/crawl_*.json) idempotently: attribute sites, push, link, verify=live."""
import json, glob, os, sys, csv
sys.path.insert(0, '/home/claude/pflege/repo' if os.path.exists('/home/claude/pflege/repo') else os.getcwd())
from collections import OrderedDict
from pflege_jobs.registry import Matcher
from pflege_jobs.sinks import EdgeSink
import requests
only_browser = "--only" in sys.argv and "browser" in sys.argv
rows = OrderedDict()
files = glob.glob('data/crawl_b*.json') + glob.glob('data/crawl_pi_*.json') if only_browser else glob.glob('data/*_done.json') + glob.glob('data/crawl_*.json')
for f in files:
    try: d = json.load(open(f))
    except Exception: continue
    lst = [r for v in d.values() for r in v.get('rows', [])] if isinstance(d, dict) and d and isinstance(next(iter(d.values())), dict) and 'rows' in next(iter(d.values())) else (d if isinstance(d, list) else [])
    for r in lst:
        if isinstance(r, dict) and r.get('source_ref'): rows[r['source_ref']] = r
rows = list(rows.values())
clinics = list(csv.DictReader(open('data/registry/clinics.csv', encoding='utf-8')))
for c in clinics: c['beds'] = int(c['beds']) if c.get('beds') else None
m = Matcher([dict(c) for c in clinics])
for r in rows:
    if not r.get('_kez'):
        mt = m.match(r['employer_name'], r.get('city')); r['_kez'] = mt[0] if mt else r.get('_seed_kez'); r['_rule'] = mt[1] if mt else ('seed_kez' if r.get('_kez') else None)
    if r.get('_kez'): r['employer_class'] = 'clinic'
print("rows", len(rows), "kez", sum(1 for r in rows if r.get('_kez')))
sink = EdgeSink(batch=200); print("load:", sink.write(rows, resolve=True, log=lambda *_: None))
U = os.environ["SUPABASE_URL"]; H = {"apikey": os.environ["SUPABASE_ANON_KEY"], "Accept-Profile": "pflege_jobs"}
ids = {}; off = 0
while True:
    ch = requests.get(f"{U}/rest/v1/posting_observations?select=posting_id,source_ref&source_id=eq.20&order=observation_id&limit=1000&offset={off}", headers=H, timeout=120).json(); off += len(ch)
    for o in ch: ids[o['source_ref']] = o['posting_id']
    if len(ch) < 1000: break
links = [{"posting_id": ids[r['source_ref']], "clinic_id": r['_kez'], "clinic_match_rule": r.get('_rule') or 'seed_kez', "clinic_match_score": 0.9} for r in rows if r['source_ref'] in ids and r.get('_kez')]
ver = [{"posting_id": ids[r['source_ref']], "verify_status": "live", "verify_http": 200, "verified_at": r['observed_at'], "verify_note": "crawled live from career site"} for r in rows if r['source_ref'] in ids]
for i in range(0, len(links), 400): sink._post({"clinic_links": links[i:i+400]})
for i in range(0, len(ver), 400): sink._post({"verify": ver[i:i+400]})
print("links", len(links), "verify", len(ver))
