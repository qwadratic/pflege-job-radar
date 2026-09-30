import json, sys
sys.path.insert(0, '.')
from app import config as A
from pflege_jobs.sources import pi_asp
from pflege_jobs.classify import norm_text
towns={norm_text(r['town']) for r in A.rest_get_all('clinics',{'select':'town','order':'clinic_id'}) if r['town']}
rows = []
for s in json.load(open('data/registry/pi_seeds.json')):
    r, st = pi_asp.crawl(s, towns); rows += r
json.dump(rows, open('data/crawl_pi_all.json', 'w'), ensure_ascii=False); print("pi rows", len(rows))
