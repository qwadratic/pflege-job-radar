"""Adapters for the ATS vendors that had no crawler yet.

After the second-pass discovery labelled 175 of 403 census sites, these vendors were still
uncovered: personio (3), smartrecruiters (1), helix (1+), concludis (7), oracle (4), talention (1)
and the WordPress/TYPO3 "jobs" post-type sites the census calls typo3_jobs (29).

Each adapter returns the same inbox-shaped rows as the other crawlers, so
crawlers/load_crawl_output.py ingests them unchanged:
    {kind, source_host, source_url, payload{title,org,loc[],url,page,description,...}, collector, client_id}

  python crawlers/vendor_adapters.py                 # every labelled site whose vendor is supported
  python crawlers/vendor_adapters.py personio        # one vendor
  python crawlers/vendor_adapters.py --list          # what is supported and how many sites

How each vendor is reached (probed 2026-09-06):
  personio        <slug>.jobs.personio.de/xml — public XML feed, full descriptions. Documented.
  smartrecruiters api.smartrecruiters.com/v1/companies/<id>/postings — public JSON, paginated.
  helix           <tenant>.helixjobs.com/<unit>/joblist -> /jobad?prj=<id> links, server-rendered.
  concludis       In this census the "concludis" label mostly sits on WordPress career sites that
                  expose a jobs post-type sitemap (wp-sitemap-posts-jobs-N.xml). Real *.concludis.de
                  tenants are handled by the same sitemap+detail path.
  wp_jobs         Same shape, used for the typo3_jobs/WordPress sites: find a job sitemap, then read
                  <title>/<h1> from each detail page. No JSON-LD on these, hence the HTML fallback.
  oracle          Oracle Recruiting Cloud is a JS SPA behind an XHR API; its sites here (Altmühlfranken,
                  St. Josef) are already covered better by crawlers/portals.py, so they are routed
                  there rather than duplicated. Klinikum FFB is a plain career page -> wp_jobs.
"""
import argparse
import html as _html
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"}
OUT = os.environ.get("EGRESS_OUTPUT_DIR", "crawl_vendors")
CID = "vendor-adapters-" + os.environ.get("EGRESS_CLIENT", "default")
PROJECT = os.environ.get("SUPABASE_PROJECT_URL", "https://klkxfvieaxpjlplloljn.supabase.co")

GENDER = re.compile(r"\((?:m|w|d|x|i|gn)\s?[/|*]\s?(?:m|w|d|x|i|gn)(?:\s?[/|*]\s?(?:m|w|d|x|i|gn))?\)", re.I)


def get(u, timeout=30, session=None):
    try:
        return (session or requests).get(u, headers=H, timeout=timeout, allow_redirects=True)
    except Exception:
        return None


def _txt(s, limit=20000):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()[:limit] or None


def row(host, url, payload, vendor):
    return {"kind": "jobposting", "source_host": host, "source_url": url,
            "payload": payload, "collector": "vendor-%s-v1" % vendor, "client_id": CID}


# ---------------------------------------------------------------------------
# personio: <slug>.jobs.personio.de/xml
# ---------------------------------------------------------------------------
def personio_slug(careers_url, session=None):
    m = re.search(r"https?://([a-z0-9\-]+)\.jobs\.personio\.(?:de|com)", careers_url or "", re.I)
    if m:
        return m.group(1)
    r = get(careers_url, session=session)
    if r and r.ok:
        m = re.search(r"([a-z0-9\-]+)\.jobs\.personio\.(?:de|com)", r.text, re.I)
        if m:
            return m.group(1)
    return None


def parse_personio_xml(xml, org, page_url):
    """<position> blocks; jobDescriptions carry the body, office/subcompany the location."""
    out = []
    for m in re.finditer(r"<position>(.*?)</position>", xml or "", re.S):
        b = m.group(1)
        def f(tag):
            x = re.search(r"<%s>(.*?)</%s>" % (tag, tag), b, re.S)
            return _html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", x.group(1)).strip()) if x else None
        pid, name = f("id"), f("name")
        if not name:
            continue
        desc = " ".join(_txt(v) or "" for v in re.findall(r"<value>(.*?)</value>", b, re.S))
        # <office> is a free-text site label ("Maximilians-Augenklinik"), not necessarily a town;
        # the caller fills in the clinic's own town when we leave this empty.
        office = f("office")
        city = office if office and not re.search(r"klinik|haus|zentrum|standort|gmbh", office, re.I) else None
        out.append({"title": name, "org": f("subcompany") or org,
                    "loc": [{"city": city, "plz": None, "region": None}],
                    "url": "%s/job/%s" % (page_url.rstrip("/").replace("/xml", ""), pid) if pid else page_url,
                    "page": page_url, "employmentType": f("employmentType"),
                    "datePosted": (f("createdAt") or "")[:10] or None,
                    "description": desc[:20000] or None})
    return out


def crawl_personio(c, session=None):
    """Prefer the XML feed; fall back to the site's own job pages.

    Some sites the census labelled "personio" only run the Personio *WordPress plugin* (ProSomno) or
    turned out not to be Personio at all (barmherzige.net). They still publish jobs under their own
    /stelle/ or /jobs/ URLs, so falling through to the generic crawler beats returning nothing.
    """
    slug = personio_slug(c.get("careers_url"), session)
    if slug:
        base = "https://%s.jobs.personio.de" % slug
        r = get(base + "/xml", session=session)
        if r and r.ok:
            # the feed declares UTF-8 in its XML prolog but often ships no charset header, and
            # requests then falls back to latin-1 -> "fÃ¼r". Trust the document, not the guess.
            r.encoding = "utf-8"
            jobs = parse_personio_xml(r.text, c["name"], base + "/xml")
            if jobs:
                host = "%s.jobs.personio.de" % slug
                return [row(host, j["url"], j, "personio") for j in jobs]
    return crawl_wp_jobs(c, session=session)


# ---------------------------------------------------------------------------
# smartrecruiters: api.smartrecruiters.com/v1/companies/<id>/postings
# ---------------------------------------------------------------------------
def parse_smartrecruiters(data, org, page_url):
    out = []
    for p in data.get("content") or []:
        loc = p.get("location") or {}
        out.append({"title": p.get("name"), "org": (p.get("company") or {}).get("name") or org,
                    "loc": [{"city": loc.get("city"), "plz": loc.get("postalCode"), "region": loc.get("region")}],
                    "url": p.get("ref") or p.get("applyUrl") or
                           "https://jobs.smartrecruiters.com/%s/%s" % ((p.get("company") or {}).get("identifier", ""), p.get("id", "")),
                    "page": page_url, "datePosted": (p.get("releasedDate") or "")[:10] or None,
                    "employmentType": ((p.get("typeOfEmployment") or {}).get("label")),
                    "description": None})
    return out


def crawl_smartrecruiters(c, session=None):
    cu = c.get("careers_url") or ""
    ident = None
    m = re.search(r"smartrecruiters\.com/([A-Za-z0-9\-_]+)", cu)
    if m:
        ident = m.group(1)
    else:
        r = get(cu, session=session)
        if r and r.ok:
            m = re.search(r"smartrecruiters\.com/([A-Za-z0-9\-_]+)", r.text)
            ident = m.group(1) if m else None
    if not ident:
        return crawl_wp_jobs(c, session=session)
    url = "https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100" % ident
    r = get(url, session=session)
    if not r or not r.ok:
        return crawl_wp_jobs(c, session=session)
    try:
        data = r.json()
    except Exception:
        return crawl_wp_jobs(c, session=session)
    out = [row("jobs.smartrecruiters.com", j["url"], j, "smartrecruiters")
           for j in parse_smartrecruiters(data, c["name"], url) if j.get("title") and j.get("url")]
    return out or crawl_wp_jobs(c, session=session)


# ---------------------------------------------------------------------------
# helix: <tenant>.helixjobs.com/<unit>/joblist -> /jobad?prj=<id>
# ---------------------------------------------------------------------------
def parse_helix(htmltext, base, org, page_url):
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="((?:/[^"]*)?/?jobad\?prj=([A-Za-z0-9]+))"[^>]*>(.*?)</a>',
                         htmltext or "", re.S | re.I):
        prj = m.group(2)
        if prj in seen:
            continue
        title = _txt(m.group(3), 300)
        if not title:
            continue
        seen.add(prj)
        out.append({"title": title, "org": org, "loc": [{"city": None, "plz": None, "region": None}],
                    "url": urljoin(base, m.group(1)), "page": page_url, "description": None})
    return out


def crawl_helix(c, session=None):
    cu = c.get("careers_url") or ""
    r = get(cu, session=session)
    if not r or not r.ok:
        return []
    jobs = parse_helix(r.text, r.url, c["name"], r.url)
    if not jobs:                                     # career page may only link to the joblist
        for m in re.finditer(r'href="([^"]*(?:joblist|helixjobs[^"]*)[^"]*)"', r.text, re.I):
            r2 = get(urljoin(r.url, m.group(1)), session=session)
            if r2 and r2.ok:
                jobs = parse_helix(r2.text, r2.url, c["name"], r2.url)
                if jobs:
                    break
    if not jobs:
        return crawl_wp_jobs(c, session=session)     # not a helix tenant after all
    host = urlparse(cu).netloc
    return [row(host, j["url"], j, "helix") for j in jobs]


# ---------------------------------------------------------------------------
# WordPress / TYPO3 "jobs" sites + concludis tenants: sitemap -> detail pages
# ---------------------------------------------------------------------------
JOB_SITEMAP = re.compile(r"(jobs?|stellen|karriere|career|vacan)", re.I)
JOB_PATH = re.compile(r"/(jobs?|stellen?|stellenangebote?|stellenanzeige|karriere/stellen|vacan)[/-]", re.I)


def find_job_urls(base, session=None, max_maps=8):
    """Follow robots.txt + sitemap indexes, prefer a jobs-specific sitemap, return job detail URLs."""
    maps, out = [], []
    r = get(urljoin(base, "/robots.txt"), timeout=15, session=session)
    if r and r.ok:
        maps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
    maps += [urljoin(base, p) for p in ("/sitemap.xml", "/wp-sitemap.xml", "/sitemap_index.xml")]
    seen, queue, n = set(), list(dict.fromkeys(maps)), 0
    while queue and n < max_maps:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u); n += 1
        r = get(u, timeout=25, session=session)
        if not r or not r.ok or "<" not in (r.text[:200] or ""):
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text, re.I)
        if "<sitemapindex" in r.text[:900].lower():
            job_maps = [l for l in locs if JOB_SITEMAP.search(l)]
            queue = job_maps + queue if job_maps else queue + locs[:3]
        else:
            out += locs
    return [u for u in dict.fromkeys(out) if JOB_PATH.search(u)]


def parse_job_page(htmltext, url, org):
    """No JSON-LD on these sites: take JSON-LD if present, else <h1>, else <title>."""
    for m in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', htmltext or "", re.S):
        try:
            d = json.loads(m)
        except Exception:
            continue
        stack = d if isinstance(d, list) else [d]
        while stack:
            n = stack.pop()
            if not isinstance(n, dict):
                continue
            if "JobPosting" in str(n.get("@type") or ""):
                a = ((n.get("jobLocation") or {}) if isinstance(n.get("jobLocation"), dict)
                     else (n.get("jobLocation") or [{}])[0]).get("address") or {}
                o = n.get("hiringOrganization") or {}
                return {"title": n.get("title"), "org": (o.get("name") if isinstance(o, dict) else o) or org,
                        "loc": [{"city": a.get("addressLocality"), "plz": a.get("postalCode"),
                                 "region": a.get("addressRegion")}],
                        "url": n.get("url") or url, "page": url,
                        "datePosted": (n.get("datePosted") or "")[:10] or None,
                        "description": _txt(n.get("description"))}
            stack += [v for v in n.values() if isinstance(v, (dict, list))]
            stack += [x for v in n.values() if isinstance(v, list) for x in v if isinstance(x, dict)]
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", htmltext or "", re.S)
    title = _txt(h1.group(1), 300) if h1 else None
    if title:
        title = re.sub(r"^(Bewirb dich als|Jetzt bewerben als|Stellenangebot:?)\s+", "", title, flags=re.I)
    if not title or not GENDER.search(title):
        t = re.search(r"<title>(.*?)</title>", htmltext or "", re.S)
        cand = _txt(t.group(1), 300) if t else None
        if cand:
            cand = re.split(r"\s+[–—|]\s+", cand)[0].strip()
            if GENDER.search(cand) or not title:
                title = cand
    if not title:
        return None
    body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", htmltext or "")
    return {"title": title, "org": org, "loc": [{"city": None, "plz": None, "region": None}],
            "url": url, "page": url, "description": _txt(body)}


def crawl_wp_jobs(c, session=None, max_jobs=int(os.environ.get("VENDOR_MAX_JOBS", "300"))):
    cu = (c.get("careers_url") or "").strip()
    if not cu:
        return []
    p = urlparse(cu)
    base = "%s://%s" % (p.scheme, p.netloc)
    urls = find_job_urls(base, session=session)
    if not urls:                                      # fall back to job links on the career page
        r = get(cu, session=session)
        if r and r.ok:
            urls = [urljoin(r.url, h) for h in re.findall(r'href="([^"#]+)"', r.text) if JOB_PATH.search(h)]
            urls = list(dict.fromkeys(urls))
    out, host = [], p.netloc
    for u in urls[:max_jobs]:
        r = get(u, session=session)
        if not r or not r.ok:
            continue
        j = parse_job_page(r.text, r.url, c["name"])
        if not j or not j.get("title"):
            continue
        if not j["loc"][0]["city"] and c.get("town"):
            j["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
        out.append(row(host, j["url"], j, "wp_jobs"))
        time.sleep(0.2)
    return out


def crawl_rexx(c, session=None, max_jobs=int(os.environ.get("VENDOR_MAX_JOBS", "300"))):
    """rexx systems: a server-rendered listing whose job links end in `-<lang>-j<id>.html`.

    No JSON/feed endpoint is exposed (api/, f=json both 404), but the listing needs no JavaScript,
    so a plain fetch + one request per detail page is enough. The `-j<id>` suffix is the stable
    identifier; the rest of the slug is the (mutable) title, so dedupe on it.
    """
    cu = (c.get("careers_url") or "").strip()
    if not cu:
        return []
    # The listing shows 100 jobs at a time and pages with ?start=N (no visible pager on some skins),
    # so a single fetch silently truncates the biggest boards at exactly 100. Page until no new ids.
    urls, seen, base = [], set(), None
    for start in range(0, 1000, 100):
        page_url = cu if start == 0 else cu + ("&" if "?" in cu else "?") + "start=%d" % start
        r = get(page_url, session=session)
        if not r or not r.ok:
            break
        if base is None:
            base = "%s://%s" % (urlparse(r.url).scheme, urlparse(r.url).netloc)
        fresh = 0
        for h in re.findall(r'href="([^"#]*-j\d+\.html[^"]*)"', r.text):
            u = urljoin(r.url, _html.unescape(h))
            jid = re.search(r"-j(\d+)\.html", u)
            if not jid or jid.group(1) in seen:
                continue
            seen.add(jid.group(1))
            urls.append(u)
            fresh += 1
        if fresh == 0 or len(urls) >= max_jobs:
            break
        time.sleep(0.2)
    if base is None:
        return []
    out = []
    for u in urls[:max_jobs]:
        d = get(u, session=session)
        if not d or not d.ok:
            continue
        j = parse_job_page(d.text, d.url, c["name"])
        if not j or not j.get("title"):
            continue
        if not j["loc"][0]["city"] and c.get("town"):
            j["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
        out.append(row(urlparse(base).netloc, j["url"], j, "rexx"))
        time.sleep(0.2)
    return out


# mein-check-in.de hosts one tenant per employer; the careers page just links into it.
MCI_TENANT = re.compile(r"mein-check-in\.de/([a-z0-9][a-z0-9_-]*)/", re.I)


def crawl_mein_check_in(c, session=None, max_jobs=int(os.environ.get("VENDOR_MAX_JOBS", "300"))):
    """mein-check-in: resolve the tenant slug, then read /<tenant>/overview.

    The clinic's own careers page is usually a thin wrapper that links to
    `mein-check-in.de/<tenant>/index`; the listing lives on that host and is server-rendered, with
    each vacancy as `position-<id>`. Titles are already in the listing anchors, so the detail fetch
    is only needed for the description -- keeping the crawl to ~1 request per job.
    """
    cu = (c.get("careers_url") or "").strip()
    tenant = None
    m = MCI_TENANT.search(cu)
    if m:
        tenant = m.group(1)
    else:                                             # follow the careers page and look for the link
        r = get(cu, session=session) if cu else None
        if r and r.ok:
            m = MCI_TENANT.search(r.text)
            tenant = m.group(1) if m else None
    if not tenant:
        return []
    host = "www.mein-check-in.de"
    listing = get("https://%s/%s/overview" % (host, tenant), session=session)
    if not listing or not listing.ok:
        return []
    seen, out = set(), []
    for pid, inner in re.findall(r'<a[^>]+position-(\d+)[^>]*>(.*?)</a>', listing.text, re.S):
        if pid in seen:
            continue
        seen.add(pid)
        title = _txt(inner, 300)
        if not title:
            continue
        u = "https://%s/%s/position-%s" % (host, tenant, pid)
        j = {"title": title, "org": c["name"],
             "loc": [{"city": c.get("town"), "plz": None, "region": "BAYERN"}],
             "url": u, "page": u, "description": None}
        d = get(u, session=session)
        if d and d.ok:
            full = parse_job_page(d.text, u, c["name"])
            if full and full.get("description"):
                j["description"] = full["description"]
        out.append(row(host, u, j, "mein-check-in"))
        if len(out) >= max_jobs:
            break
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------------------
# Group portals: many census sites share one employer-group job board
# ---------------------------------------------------------------------------
# Nine kbo clinics all point at kbo.de's TYPO3/Solr board, whose job URLs live on the *group*
# domain, not on each clinic's own host — so the per-site sitemap walk finds nothing. Listing pages
# are paginated with tx_solr[page]; detail pages carry a complete JSON-LD JobPosting.
GROUP_PORTALS = [
    {"match": r"^kbo-|kbo\.de|kbo-isk|kbo-heckscher|kbo-iak|kbo-lech-mangfall",
     "list": "https://kbo.de/karriere/jobboerse",
     "page_param": "tx_solr%5Bpage%5D", "pages": 12,
     "job_rx": r"https://kbo\.de/karriere/jobs/[^\"'\s>]+", "host": "kbo.de"},
    # Barmherzige Brüder run one board for all their Bavarian houses.
    {"match": r"barmherzige", "list": "https://karriere.barmherzige.net/jobs/",
     "page_param": "c_page", "pages": 12,
     "job_rx": r"https://karriere\.barmherzige\.net/jobs/[a-z0-9][^\"'\s>?]+", "host": "karriere.barmherzige.net"},
]


def group_portal_for(c):
    name = (c.get("name") or "") + " " + (c.get("careers_url") or "")
    for g in GROUP_PORTALS:
        if re.search(g["match"], name, re.I):
            return g
    return None


def crawl_group_portal(c, g, session=None, max_jobs=250):
    """Page the group board, then read each job's JSON-LD. Shared across every site of the group."""
    urls, seen = [], set()
    for i in range(1, g.get("pages", 8) + 1):
        u = g["list"] if i == 1 else "%s?%s=%d" % (g["list"], g["page_param"], i)
        r = get(u, session=session)
        if not r or not r.ok:
            break
        found = [x for x in re.findall(g["job_rx"], r.text)]
        fresh = [x for x in found if x not in seen]
        if not fresh:
            break
        seen.update(fresh); urls += fresh
        if len(urls) >= max_jobs:
            break
        time.sleep(0.2)
    out = []
    for u in urls[:max_jobs]:
        r = get(u, session=session)
        if not r or not r.ok:
            continue
        j = parse_job_page(r.text, r.url, c["name"])
        if j and j.get("title"):
            out.append(row(g["host"], j["url"], j, "group"))
        time.sleep(0.15)
    return out


VENDORS = {
    "rexx": crawl_rexx,
    "mein-check-in": crawl_mein_check_in,
    "personio": crawl_personio,
    "smartrecruiters": crawl_smartrecruiters,
    "helix": crawl_helix,
    "concludis": crawl_wp_jobs,
    "typo3_jobs": crawl_wp_jobs,
    "talention": crawl_wp_jobs,
    "oracle": crawl_wp_jobs,
}


def load_clinics(vendors):
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ["SUPABASE_ANON_KEY"]
    r = requests.get(PROJECT + "/rest/v1/clinics?select=clinic_id,name,town,website,careers_url,ats_type"
                     "&ats_type=in.(%s)&limit=1000" % ",".join(vendors),
                     headers={"apikey": key, "Authorization": "Bearer " + key,
                              "Accept-Profile": "pflege_jobs"}, timeout=60)
    r.raise_for_status()
    return [c for c in r.json() if (c.get("careers_url") or "").strip()]


def save(rows, tag):
    if not rows:
        return 0
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "%s_%s.jsonl" % (tag, time.strftime("%Y%m%dT%H%M%S")))
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vendors", nargs="*", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    want = [v for v in (a.vendors or list(VENDORS)) if v in VENDORS]
    if a.list:
        cl = load_clinics(list(VENDORS))
        from collections import Counter
        c = Counter(x["ats_type"] for x in cl)
        for v in VENDORS:
            print("%-18s %s sites with a careers_url" % (v, c.get(v, 0)))
        return
    clinics = load_clinics(want)
    if a.limit:
        clinics = clinics[:a.limit]
    print("%d sites across vendors %s" % (len(clinics), want))
    total = 0
    session = requests.Session(); session.headers.update(H)
    group_done = {}
    # Several operators point every site at one shared board (Schön Klinik: 7 sites -> 1 rexx board;
    # RHÖN: 2; Kliniken Südostbayern: 3). Fetching per site multiplied 490 real jobs into 1,319 rows.
    # Key the cache by careers_url so a shared board is crawled once and attributed to the first site;
    # link-clinics then spreads it across the group by employer/town like any other multi-site source.
    url_done = {}
    for c in clinics:
        g = group_portal_for(c)
        fn = VENDORS[c["ats_type"]]
        try:
            if g:
                # one fetch per group, reused by every member site (kbo: 9 clinics, 1 board)
                key = g["list"]
                if key not in group_done:
                    group_done[key] = crawl_group_portal(c, g, session=session)
                    rows = group_done[key]
                else:
                    rows = []
                    print("  %-44s %-16s (shared group board, already fetched)" % (c["name"][:44], c["ats_type"]))
            else:
                key = (c.get("careers_url") or "").strip().lower()
                if key and key in url_done:
                    print("  %-44s %-16s (same board as %s, already fetched)"
                          % (c["name"][:44], c["ats_type"], url_done[key]))
                    rows = []
                else:
                    rows = fn(c, session=session)
                    if key:
                        url_done[key] = c["name"][:28]
        except Exception as e:
            print("  %-44s FAILED %s" % (c["name"][:44], str(e)[:70])); continue
        for r in rows:                                # registry town beats an empty/vendor-specific one
            locs = r["payload"].get("loc") or [{}]
            if c.get("town") and not any((l or {}).get("city") for l in locs):
                r["payload"]["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
        n = save(rows, "vendor_" + c["ats_type"])
        total += n
        print("  %-44s %-16s jobs %3d" % (c["name"][:44], c["ats_type"], n))
    print("total rows saved: %d -> %s" % (total, OUT))


if __name__ == "__main__":
    main()
