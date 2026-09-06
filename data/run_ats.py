import json, sys, csv, time
sys.path.insert(0,'/home/claude/pflege/repo')
from pflege_jobs.sources.ats_seeds import BUILDERS
from pflege_jobs.sources.career_crawl import Crawler
from pflege_jobs.classify import norm_text
from pflege_jobs.registry import Matcher
ats=sys.argv[1]; budget=int(sys.argv[2]) if len(sys.argv)>2 else 120
towns={norm_text(o['city']) for o in json.load(open('data/raw.json'))['observations'] if o.get('city')}
towns|={norm_text(r['town']) for r in csv.DictReader(open('data/registry/clinics.csv',encoding='utf-8')) if r['town']}
clinics=list(csv.DictReader(open('data/registry/clinics.csv',encoding='utf-8')))
for c in clinics: c['beds']=int(c['beds']) if c.get('beds') else None
m=Matcher([dict(c) for c in clinics]); byid={c['clinic_id']:c for c in clinics}
cen=[v for v in json.load(open('data/registry/ats_census.json')).values() if v['ats']==ats and v['career']]
path=f'data/ats_{ats}_done.json'
try: done=json.load(open(path))
except Exception: done={}
cr=Crawler(towns,per_site_pages=budget,list_pages=8,sleep=0.2)
seen_lists={v['list'] for v in done.values() if v.get('list')}
for f in cen:
    if str(f['kr_id']) in done: continue
    mt=m.match(f['name'],f['city']); kez=mt[0] if mt else None; town=byid[kez]['town'] if kez else f['city']
    seed=BUILDERS[ats](f,kez,town)
    if not seed: done[str(f['kr_id'])]={"name":f['name'],"list":None,"rows":[],"stats":{"error":"no seed"}}; json.dump(done,open(path,'w'),ensure_ascii=False); print(f['name'][:30],'no seed'); continue
    if seed['career'] in seen_lists:  # shared portal: rows already collected under the first facility; attribution by city later
        done[str(f['kr_id'])]={"name":f['name'],"list":seed['career'],"rows":[],"stats":{"shared":True}}; json.dump(done,open(path,'w'),ensure_ascii=False); continue
    seen_lists.add(seed['career']); t=time.time()
    try: rows,st=cr.crawl(seed)
    except Exception as e: rows,st=[],{"error":str(e)[:80]}
    for r in rows: r['_seed_kez']=kez; r['_seed_town']=town
    done[str(f['kr_id'])]={"name":f['name'],"list":seed['career'],"kez":kez,"rows":rows,"stats":st,"secs":round(time.time()-t)}
    json.dump(done,open(path,'w'),ensure_ascii=False)
    print(f"{f['name'][:28]:<28} {seed['career'][:48]:<48} lists {st.get('list_pages','?')} links {st.get('job_links_found','?'):>3} fetched {st.get('job_pages','?'):>3} jsonld {st.get('jobposting_pages','?'):>3} -> {len(rows):>3} (nonBY {st.get('dropped_non_bavaria','?')}, noloc {st.get('dropped_unknown_loc','?')}) {round(time.time()-t)}s", flush=True)
