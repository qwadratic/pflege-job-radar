import json, csv, sys, time
sys.path.insert(0,'.')
from pflege_jobs.sources.career_browser import BrowserCrawler
from pflege_jobs.sources.career_crawl import Crawler
from pflege_jobs.classify import norm_text
seeds=[s for s in json.load(open('data/registry/js_seeds.json')) if s['name'] in sys.argv[1].split('|')]
towns={norm_text(r['town']) for r in csv.DictReader(open('data/registry/clinics.csv',encoding='utf-8')) if r['town']}
towns={t.split(',')[0].strip() for t in towns}
bc=BrowserCrawler(towns,per_site_pages=int(sys.argv[2]) if len(sys.argv)>2 else 80,sleep=0.2); rc=Crawler(towns,per_site_pages=120,sleep=0.2)
allrows=[]
for s in seeds:
    t=time.time()
    rows,st=(bc if s.get('browser') else rc).crawl(s)
    print(f"{s['name'][:34]:<34} lists {st['list_pages']} links {st['job_links_found']:>3} fetched {st['job_pages']:>3} jsonld {st['jobposting_pages']:>3} heur {st['heuristic_pages']:>3} -> {len(rows):>3} rows {time.time()-t:.0f}s")
    allrows+=rows
bc.close()
tag='b'+str(sum(map(ord,sys.argv[1]))%100000)
json.dump(allrows,open(f'data/crawl_{tag}.json','w'),ensure_ascii=False); print("saved",tag,len(allrows))
