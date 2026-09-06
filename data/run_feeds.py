"""Feed/API adapters in one go: Personio, SmartRecruiters, Talention (+ HELIX/misc seeds via generic crawler are in run_crawl)."""
import json, csv, sys
sys.path.insert(0, '.')
from pflege_jobs.sources import feeds
from pflege_jobs.classify import norm_text
towns={norm_text(r['town']) for r in csv.DictReader(open('data/registry/clinics.csv', encoding='utf-8')) if r['town']}
seeds = json.load(open('data/registry/feed_seeds.json')); rows = []
for s in seeds:
    try: rows += getattr(feeds, s['kind'])(s, towns)
    except Exception as e: print("feed failed", s['name'], str(e)[:80])
json.dump(rows, open('data/crawl_feeds.json', 'w'), ensure_ascii=False); print("feed rows", len(rows))
