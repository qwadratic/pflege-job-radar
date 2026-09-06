"""ATS census: klinikradar facility -> website -> career page -> fingerprint. Resumable (checkpoint json)."""
import json, re, sys, time, requests
from urllib.parse import urljoin, urlparse
sys.path.insert(0,'/home/claude/pflege/repo')
from pflege_jobs.sources.bite import detect as bite_detect
H={"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
CAREER=re.compile(r"karriere|stellen|jobs?\b|bewerb|arbeiten-bei|career|job-?boerse|stellenangebot",re.I)
ATS=[("softgarden",r"softgarden"),("dvinci",r"dvinci"),("personio",r"personio"),("smartrecruiters",r"smartrecruiters"),("rexx",r"rexx-systems|\brexx\b"),("onlyfy",r"onlyfy|prescreen"),("umantis",r"umantis"),("concludis",r"concludis"),("talention",r"talention"),("successfactors",r"successfactors|jobs2web"),("interamt",r"interamt"),("mein-check-in",r"mein-check-in"),("oracle",r"oraclecloud|taleo"),("workday",r"myworkday"),("bewerberportal_helios",r"helios-gesundheit\.de/karriere"),("typo3_jobs",r"tx_[a-z]*job|typo3conf/ext/[a-z_]*job"),("jobiqo",r"jobiqo"),("coveto",r"coveto"),("bite_jobs",r"jobs\.b-ite\.com|/jobposting/[0-9a-f]{40}")]
def get(u,t=25):
    try: return requests.get(u,headers=H,timeout=t,allow_redirects=True)
    except Exception: return None
def fingerprint(html,url):
    d=bite_detect(html or "")
    if d: return "bite", d
    for n,p in ATS:
        if re.search(p,html or "",re.I) or re.search(p,url or "",re.I): return n, None
    return None, None
kr=json.load(open('data/registry/klinikradar_bayern.json'))
i0,i1=int(sys.argv[1]),int(sys.argv[2]); out={}
try: out=json.load(open('data/registry/ats_census.json'))
except Exception: pass
for o in kr[i0:i1]:
    k=str(o['kr_id'])
    if k in out: continue
    row={"kr_id":o['kr_id'],"name":o['name'],"city":o['city'],"beds":o['nr_beds'],"website":None,"career":None,"ats":None,"bite":None}
    r=get(o['url'])
    if r and r.ok:
        m=re.search(r'<script data-page="app" type="application/json">(.*?)</script>',r.text,re.S)
        if m:
            try:
                w=(json.loads(m.group(1)).get('props',{}).get('hospital') or {}).get('website')
                if w and w.startswith('http'): row["website"]=re.sub(r'^(https?://[^/]+).*$',r'\1',w)
            except Exception: pass
    if row["website"]:
        hp=get(row["website"])
        if hp and hp.ok:
            a,_=fingerprint(hp.text,hp.url)
            if a: row["ats"]=a; row["bite"]=_
            links=re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>',hp.text,re.S|re.I)
            c=[urljoin(hp.url,h) for h,t in links if (CAREER.search(h) or CAREER.search(re.sub(r'<[^>]+>','',t))) and not re.search(r'\.(pdf|jpg|png)$',h,re.I)]
            c=[x for x in c if urlparse(x).netloc]
            c.sort(key=lambda x:(0 if re.search(r'stellen|jobs|angebot',x,re.I) else 1,len(x)))
            tried=0
            for cand in dict.fromkeys(c):
                if tried>=3 or row["ats"]=="bite": break
                cp=get(cand); tried+=1
                if cp and cp.ok:
                    a,d=fingerprint(cp.text,cp.url)
                    if a=="bite" or (a and row["ats"] in (None,"typo3_jobs")): row["ats"]=a; row["bite"]=d
                    if not row["career"]: row["career"]=cp.url
    out[k]=row; json.dump(out,open('data/registry/ats_census.json','w'),ensure_ascii=False); time.sleep(0.2)
from collections import Counter
print(f"done {i0}-{i1}; total scanned {len(out)}; ats:",Counter(v['ats'] for v in out.values()).most_common())
