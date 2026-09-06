import json, csv, sys
sys.path.insert(0,'.')
from pflege_jobs.sources.career_crawl import crawl_all
from pflege_jobs.classify import norm_text
import os
seeds=json.load(open(os.environ.get('SEEDS','data/registry/top20_seeds.json')))
i0,i1=int(sys.argv[1]),int(sys.argv[2]); seeds=seeds[i0:i1] if len(sys.argv)<5 else [s for s in seeds if s['name'] in sys.argv[4].split('|')]
towns={norm_text(r['town']) for r in csv.DictReader(open('data/registry/clinics.csv',encoding='utf-8')) if r['town']}
towns={t.split(',')[0].strip() for t in towns}
rows,report=crawl_all(seeds,towns,budget=int(sys.argv[3]) if len(sys.argv)>3 else 120)
tag=f'{i0}_{i1}' if len(sys.argv)<5 else 'n'+str(sum(map(ord,sys.argv[4]))%100000)
json.dump(rows,open(f'data/crawl_{tag}.json','w'),ensure_ascii=False); json.dump(report,open(f'data/crawl_report_{tag}.json','w'),ensure_ascii=False,indent=1)
