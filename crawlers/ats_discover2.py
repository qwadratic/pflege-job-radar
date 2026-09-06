"""Second-pass ATS discovery for census sites that the first pass left unlabeled.

The first pass walked homepage -> career link -> fingerprint and labelled 145 of 403 sites. The 258
that stayed empty fall into three groups, and this pass attacks each with its own angle:

  1. stepstone   The site advertises on StepStone. A StepStone ad's "Auf Website des Unternehmens
                 bewerben" button points at the employer's own ATS, so one detail page reveals the
                 vendor without ever finding the career page. Uses the ads we already crawled
                 (crawl_output/*.jsonl) — no new StepStone traffic.
  2. sitemap     Fetch /sitemap.xml (+ the sitemap index, + robots.txt Sitemap: lines) and look for
                 job URLs. Many TYPO3/WordPress clinic sites never link the career page from the
                 homepage nav but do list every job in the sitemap.
  3. bewerben    Fetch the career page and follow the "Bewerben" / "Jetzt bewerben" / "Online
                 bewerben" links. That button is the ATS hand-off; the career page itself is often
                 a plain CMS page with no vendor fingerprint at all.

Every angle ends in the same fingerprint() and emits the same inbox 'probe' row that
pflege_jobs/cli.py:cmd_inbox already understands (kind='probe', payload.probe='ats_discovery'),
so discoveries flow into clinics.ats_type through the existing loader.

  python crawlers/ats_discover2.py                      # all three angles, all unlabeled sites
  python crawlers/ats_discover2.py --angle sitemap      # one angle
  python crawlers/ats_discover2.py --limit 20           # smoke test
  python crawlers/ats_discover2.py --report             # what is still unlabeled, and why
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"}
OUT = os.environ.get("EGRESS_OUTPUT_DIR", "crawl_output")
CID = "ats-discover2-" + os.environ.get("EGRESS_CLIENT", "default")

# Vendor fingerprints, checked against both the HTML body and the URL. Ordered: the most specific
# host patterns first, so e.g. a softgarden-hosted d.vinci iframe cannot mislabel the site.
ATS = [
    ("softgarden",      r"softgarden\.io|jobdb\.softgarden|softgarden\.de"),
    ("dvinci",          r"dvinci(-hr|-easy)?\.(com|de)|dvinci"),
    ("personio",        r"jobs\.personio\.(de|com)|personio\.(de|com)"),
    ("smartrecruiters", r"smartrecruiters\.com|careers\.smartrecruiters"),
    ("rexx",            r"rexx-systems|rexx\.de|/stellenangebote\.html\?|jobs\.rexx"),
    ("umantis",         r"umantis\.com|recruitingapp-\d+"),
    ("concludis",       r"concludis\.de|\.concludis"),
    ("talention",       r"talention\.com|\.talention"),
    ("mein-check-in",   r"mein-check-in\.de"),
    ("bite",            r"jobs\.b-ite\.com|b-ite\.com|/jobposting/[0-9a-f]{40}"),
    ("oracle",          r"oraclecloud\.com|taleo\.net|/hcmUI/CandidateExperience"),
    ("workday",         r"myworkdayjobs\.com|myworkday\.com"),
    ("successfactors",  r"successfactors\.(eu|com)|jobs2web"),
    ("onlyfy",          r"onlyfy\.jobs|prescreen\.io"),
    ("interamt",        r"interamt\.de"),
    ("pi_asp",          r"pi-asp\.de|bewerber-web"),
    ("helix",           r"helixjobs\.com|\.helix"),
    ("recruitee",       r"recruitee\.com"),
    ("join",            r"join\.com/companies"),
    ("heyjobs",         r"heyjobs\.co"),
    ("jobiqo",          r"jobiqo"),
    ("coveto",          r"coveto"),
    ("guidecom",        r"guidecom\.de|gcbewerbung"),
    ("hrworks",         r"hrworks\.de"),
    ("jobware",         r"jobware\.net/bewerbung"),
    ("typo3_jobs",      r"tx_[a-z]*job|typo3conf/ext/[a-z_]*job"),
]

CAREER_RX = re.compile(r"karriere|stellen|jobs?\b|bewerb|arbeiten-bei|career|stellenangebot|stellenmarkt", re.I)
APPLY_RX = re.compile(r"jetzt\s+bewerben|online[- ]bewerb|bewerben\s+sie|hier\s+bewerben|zur\s+bewerbung|"
                      r"bewerbungsformular|auf\s+website\s+des\s+unternehmens|zum\s+stellenangebot|bewerben", re.I)
JOB_URL_RX = re.compile(r"stellenangebot|stellenanzeige|/jobs?/|/job/|karriere/|vacan|/stelle/|jobposting", re.I)
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|zip|docx?|xlsx?|mp4)(\?|$)", re.I)


def get(u, timeout=20, session=None):
    try:
        return (session or requests).get(u, headers=H, timeout=timeout, allow_redirects=True)
    except Exception:
        return None


def fingerprint(html, url=""):
    """-> (vendor, evidence) or (None, None). Checks URL first: a vendor host is stronger proof."""
    for name, pat in ATS:
        m = re.search(pat, url or "", re.I)
        if m:
            return name, (url or "")[:300]
    for name, pat in ATS:
        m = re.search(pat, html or "", re.I)
        if m:
            s = max(0, m.start() - 60)
            return name, re.sub(r"\s+", " ", (html or "")[s:m.end() + 60])[:300]
    return None, None


# ---------------------------------------------------------------------------
# angle 1: StepStone ads we already have on disk
# ---------------------------------------------------------------------------
def stepstone_candidates(crawl_dirs=("crawl_output",)):
    """employer name -> a StepStone ad URL, taken from the crawl output already on disk."""
    by_emp = {}
    for d in crawl_dirs:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".jsonl"):
                continue
            for line in open(os.path.join(d, fn), encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("kind") != "jobposting":
                    continue
                p = r.get("payload") or {}
                org = (p.get("org") or "").strip()
                if org and "stepstone" in (r.get("source_host") or ""):
                    by_emp.setdefault(org, r["source_url"])
    return by_emp


def probe_stepstone(clinic, ad_url, session=None):
    """A StepStone ad links out to the employer's ATS ('Auf Website des Unternehmens bewerben')."""
    r = get(ad_url, session=session)
    if not r or not r.ok:
        return None
    ext = [u for u in re.findall(r'href="(https?://[^"]+)"', r.text) if "stepstone" not in u.lower()]
    ats, ev = fingerprint("", " ".join(ext))
    if not ats:
        ats, ev = fingerprint(r.text, "")
    if not ats:
        return None
    apply_url = next((u for u in ext if re.search(ATS[[n for n, _ in ATS].index(ats)][1], u, re.I)), None)
    return {"ats": ats, "apply_url": apply_url, "careers_url": apply_url, "evidence": ev, "angle": "stepstone"}


# ---------------------------------------------------------------------------
# angle 2: sitemap.xml
# ---------------------------------------------------------------------------
def sitemap_urls(base, session=None, max_maps=6):
    """Collect sitemap locations: robots.txt Sitemap: lines + the usual paths, following indexes."""
    found, maps = [], []
    r = get(urljoin(base, "/robots.txt"), timeout=15, session=session)
    if r and r.ok:
        maps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
    maps += [urljoin(base, p) for p in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml")]
    seen, queue, n = set(), list(dict.fromkeys(maps)), 0
    while queue and n < max_maps:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u); n += 1
        r = get(u, timeout=20, session=session)
        if not r or not r.ok or "<" not in r.text[:200]:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text, re.I)
        if "<sitemapindex" in r.text[:800].lower():
            queue += [l for l in locs if re.search(r"job|stelle|karriere|career", l, re.I)][:3] or locs[:2]
        else:
            found += locs
    return found


def probe_sitemap(clinic, base, session=None):
    """Job URLs in the sitemap either fingerprint directly or give us a detail page to fetch."""
    locs = sitemap_urls(base, session=session)
    if not locs:
        return None
    jobs = [u for u in locs if JOB_URL_RX.search(u) and not SKIP_EXT.search(u)]
    ats, ev = fingerprint("", " ".join(jobs[:200]))
    if ats:
        return {"ats": ats, "apply_url": next((u for u in jobs if re.search(ATS[[n for n, _ in ATS].index(ats)][1], u, re.I)), None),
                "careers_url": jobs[0] if jobs else None, "evidence": ev, "angle": "sitemap",
                "n_job_urls": len(jobs)}
    for u in jobs[:4]:                                   # open a couple of job pages, fingerprint those
        r = get(u, session=session)
        if not r or not r.ok:
            continue
        ats, ev = fingerprint(r.text, r.url)
        if ats:
            return {"ats": ats, "apply_url": None, "careers_url": u, "evidence": ev,
                    "angle": "sitemap", "n_job_urls": len(jobs)}
    if jobs:                                             # no vendor, but we did find the job pages
        return {"ats": None, "apply_url": None, "careers_url": jobs[0], "evidence": None,
                "angle": "sitemap", "n_job_urls": len(jobs)}
    return None


# ---------------------------------------------------------------------------
# angle 3: "Bewerben" links on the career page
# ---------------------------------------------------------------------------
def probe_bewerben(clinic, career, session=None, max_follow=4):
    """The apply button is the ATS hand-off even when the career page itself is plain CMS HTML."""
    r = get(career, session=session)
    if not r or not r.ok:
        return None
    ats, ev = fingerprint(r.text, r.url)
    if ats:
        return {"ats": ats, "apply_url": None, "careers_url": r.url, "evidence": ev, "angle": "bewerben"}
    anchors = re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', r.text, re.S | re.I)
    cands, seen = [], set()
    for href, text in anchors:
        label = re.sub(r"<[^>]+>", " ", text)
        if SKIP_EXT.search(href):
            continue
        if APPLY_RX.search(label) or APPLY_RX.search(href) or JOB_URL_RX.search(href):
            u = urljoin(r.url, href)
            if u not in seen and u != r.url:
                seen.add(u); cands.append(u)
    # off-site links are the interesting ones — that IS the vendor
    host = urlparse(r.url).netloc
    cands.sort(key=lambda u: urlparse(u).netloc == host)
    ats, ev = fingerprint("", " ".join(cands[:80]))
    if ats:
        return {"ats": ats, "apply_url": next((u for u in cands if re.search(ATS[[n for n, _ in ATS].index(ats)][1], u, re.I)), None),
                "careers_url": r.url, "evidence": ev, "angle": "bewerben"}
    for u in cands[:max_follow]:
        rr = get(u, session=session)
        if not rr or not rr.ok:
            continue
        ats, ev = fingerprint(rr.text, rr.url)
        if ats:
            return {"ats": ats, "apply_url": rr.url, "careers_url": r.url, "evidence": ev, "angle": "bewerben"}
    return None


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def load_clinics():
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"]
    r = requests.get(url + "/rest/v1/clinics?select=clinic_id,name,town,website,careers_url,ats_type&limit=1000",
                     headers={"apikey": key, "Accept-Profile": "pflege_jobs"}, timeout=60)
    r.raise_for_status()
    return r.json()


def base_of(u):
    p = urlparse(u or "")
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else None


def discover_one(c, ss_by_emp, angles):
    """Try each angle in turn for one clinic; first hit wins."""
    session = requests.Session(); session.headers.update(H)
    career = (c.get("careers_url") or "").strip()
    site = (c.get("website") or "").strip()
    base = base_of(career) or base_of(site)
    tried = []

    if "stepstone" in angles:
        ad = next((u for e, u in ss_by_emp.items() if _match_name(c["name"], e)), None)
        if ad:
            tried.append("stepstone")
            try:
                hit = probe_stepstone(c, ad, session)
                if hit and hit.get("ats"):
                    return c, hit, tried
            except Exception:
                pass
    if "bewerben" in angles and career:
        tried.append("bewerben")
        try:
            hit = probe_bewerben(c, career, session)
            if hit and hit.get("ats"):
                return c, hit, tried
        except Exception:
            pass
    if "sitemap" in angles and base:
        tried.append("sitemap")
        try:
            hit = probe_sitemap(c, base, session)
            if hit and (hit.get("ats") or hit.get("careers_url")):
                return c, hit, tried
        except Exception:
            pass
    if "bewerben" in angles and site and not career:
        tried.append("bewerben:home")
        try:
            hit = probe_bewerben(c, site, session)
            if hit and hit.get("ats"):
                return c, hit, tried
        except Exception:
            pass
    return c, None, tried


_STOP = re.compile(r"\b(gmbh|ggmbh|mbh|ag|kg|e\.?\s?v\.?|klinik(um|en)?|krankenhaus|gesundheit|"
                   r"der|die|das|und|am|im|von|für|st\.?)\b", re.I)


def _tokens(s):
    return {t for t in re.split(r"[^\wäöüß]+", _STOP.sub(" ", (s or "").lower())) if len(t) > 3}


def _match_name(clinic_name, employer):
    a, b = _tokens(clinic_name), _tokens(employer)
    return bool(a and b and len(a & b) >= max(1, min(len(a), len(b)) // 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--angle", default="stepstone,bewerben,sitemap")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    clinics = load_clinics()
    unlabeled = [c for c in clinics if not (c.get("ats_type") or "").strip()]
    if a.report:
        print(f"{len(clinics)} clinics, {len(unlabeled)} unlabeled")
        print("  with careers_url:", sum(1 for c in unlabeled if (c.get("careers_url") or "").strip()))
        print("  with website    :", sum(1 for c in unlabeled if (c.get("website") or "").strip()))
        print("  with neither    :", sum(1 for c in unlabeled
                                         if not (c.get("careers_url") or "").strip() and not (c.get("website") or "").strip()))
        print("labelled:", Counter(c["ats_type"] for c in clinics if (c.get("ats_type") or "").strip()).most_common())
        return

    angles = set(a.angle.split(","))
    todo = [c for c in unlabeled if (c.get("careers_url") or c.get("website"))]
    if a.limit:
        todo = todo[:a.limit]
    ss = stepstone_candidates() if "stepstone" in angles else {}
    print(f"{len(todo)} sites to probe, angles={sorted(angles)}, stepstone ads on disk: {len(ss)} employers")

    rows, hits, stats = [], 0, Counter()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(discover_one, c, ss, angles) for c in todo]
        for i, f in enumerate(as_completed(futs), 1):
            try:
                c, hit, tried = f.result()
            except Exception as e:
                print("  worker failed:", str(e)[:80]); continue
            stats["probed"] += 1
            if hit and hit.get("ats"):
                hits += 1; stats["ats:" + hit["ats"]] += 1; stats["via:" + hit["angle"]] += 1
                print(f"  [{i:3}/{len(todo)}] {c['name'][:44]:<44} -> {hit['ats']:<16} ({hit['angle']})")
            elif hit and hit.get("careers_url"):
                stats["careers_only"] += 1
                print(f"  [{i:3}/{len(todo)}] {c['name'][:44]:<44} -> careers_url only ({hit['angle']})")
            if hit:
                rows.append({"kind": "probe", "source_host": urlparse(hit.get("careers_url") or hit.get("apply_url") or "").netloc,
                             "source_url": hit.get("apply_url") or hit.get("careers_url") or (c.get("careers_url") or c.get("website")),
                             "collector": "ats-discover2-v1", "client_id": CID,
                             "payload": {"probe": "ats_discovery", "clinic_id": c["clinic_id"],
                                         "clinic_name": c["name"], "employer": c["name"],
                                         "ats": hit.get("ats"), "apply_url": hit.get("apply_url"),
                                         "careers_url": hit.get("careers_url"),
                                         "angle": hit["angle"], "evidence": hit.get("evidence"),
                                         "n_job_urls": hit.get("n_job_urls")}})

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "ats_discover2_%s.jsonl" % time.strftime("%Y%m%dT%H%M%S"))
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nprobed {stats['probed']} sites in {time.time()-t0:.0f}s: {hits} ATS identified, "
          f"{stats['careers_only']} careers_url only -> {path}")
    for k, v in sorted(stats.items()):
        if k.startswith(("ats:", "via:")):
            print(f"   {k:<28} {v}")


if __name__ == "__main__":
    main()
