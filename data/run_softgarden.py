import json, sys, csv, time
sys.path.insert(0,'.')
from pflege_jobs.sources.softgarden import seed_for
from pflege_jobs.sources.career_crawl import Crawler
from pflege_jobs.classify import norm_text
from pflege_jobs.registry import Matcher
towns={norm_text(r['town']) for r in csv.DictReader(open('data/registry/clinics.csv',encoding='utf-8')) if r['town']}
clinics=list(csv.DictReader(open('data/registry/clinics.csv',encoding='utf-8')))
for c in clinics: c['beds']=int(c['beds']) if c.get('beds') else None
m=Matcher([dict(c) for c in clinics]); byid={c['clinic_id']:c for c in clinics}
sites=json.load(open('data/registry/softgarden_sites.json'))
try: done=json.load(open('data/softgarden_done.json'))
except Exception: done={}
cr=Crawler(towns,per_site_pages=int(sys.argv[1]) if len(sys.argv)>1 else 100,list_pages=6,sleep=0.2)
hosts_seen={v['host'] for v in done.values() if v.get('host')}
for f in sites:
    if str(f['kr_id']) in done: continue
    mt=m.match(f['name'],f['city']); kez=mt[0] if mt else None; town=byid[kez]['town'] if kez else f['city']
    seed=seed_for(f,kez,town)
    if not seed: done[str(f['kr_id'])]={"name":f['name'],"host":None,"rows":[],"stats":{"error":"no softgarden host found"}}; json.dump(done,open('data/softgarden_done.json','w'),ensure_ascii=False); print(f['name'][:30],'no host'); continue
    host=seed['hosts'][0]
    if host in hosts_seen: done[str(f['kr_id'])]={"name":f['name'],"host":host,"rows":[],"stats":{"shared_host":True}}; json.dump(done,open('data/softgarden_done.json','w'),ensure_ascii=False); continue
    hosts_seen.add(host); t=time.time()
    try: rows,st=cr.crawl(seed)
    except Exception as e: rows,st=[],{"error":str(e)[:80]}
    for r in rows: r['_kez']=kez; r['_town']=town
    done[str(f['kr_id'])]={"name":f['name'],"host":host,"kez":kez,"rows":rows,"stats":st,"secs":round(time.time()-t)}
    json.dump(done,open('data/softgarden_done.json','w'),ensure_ascii=False)
    print(f"{f['name'][:30]:<30} {host:<40} links {st.get('job_links_found','?'):>3} -> {len(rows):>3} Pflege/BY {round(time.time()-t)}s", flush=True)
