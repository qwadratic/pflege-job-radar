import json, sys, csv, time
sys.path.insert(0,'/home/claude/pflege/repo')
from pflege_jobs.sources import bite
from pflege_jobs.classify import norm_text
towns={norm_text(o['city']) for o in json.load(open('data/raw.json'))['observations'] if o.get('city')}
towns|={norm_text(r['town']) for r in csv.DictReader(open('data/registry/clinics.csv',encoding='utf-8')) if r['town']}
seeds=json.load(open('data/registry/bite_seeds.json'))
try: done=json.load(open('data/bite_done.json'))
except Exception: done={}
seen=set()
for s in seeds:
    key=f"{s['customer']}:{s['listing']}"
    if not s['customer'] or key in seen or key in done or s['listing']=='niiid': continue
    seen.add(key); t=time.time()
    try: rows,st=bite.crawl(s,towns,log=lambda *_:None); done[key]={"name":s['name'],"rows":rows,"stats":st,"secs":round(time.time()-t)}
    except Exception as e: done[key]={"name":s['name'],"rows":[],"stats":{"error":str(e)[:80]},"secs":round(time.time()-t)}
    json.dump(done,open('data/bite_done.json','w'),ensure_ascii=False)
    print(s['name'][:30], done[key]['stats'], done[key]['secs'],'s', flush=True)
