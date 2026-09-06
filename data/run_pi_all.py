import json, sys, csv
sys.path.insert(0, '.')
from pflege_jobs.sources import pi_asp
from pflege_jobs.classify import norm_text
towns={norm_text(r['town']) for r in csv.DictReader(open('data/registry/clinics.csv',encoding='utf-8')) if r['town']}
rows = []
for s in json.load(open('data/registry/pi_seeds.json')):
    r, st = pi_asp.crawl(s, towns, max_items=80); rows += r
json.dump(rows, open('data/crawl_pi_all.json', 'w'), ensure_ascii=False); print("pi rows", len(rows))
