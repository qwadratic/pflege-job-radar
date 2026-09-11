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
  smartrecruiters api.smartrecruiters.com/v1/companies/<id>/postings — public JSON, paginated, no
                  description; .../postings/<posting-id> (fetched per row) carries jobAd.sections
                  (description) and the real slugged postingUrl.
  helix           <tenant>.helixjobs.com/<unit>/joblist -> /jobad?prj=<id> links, server-rendered.
  concludis       In this census the "concludis" label mostly sits on WordPress career sites that
                  expose a jobs post-type sitemap (wp-sitemap-posts-jobs-N.xml), handled by the same
                  sitemap+detail path as wp_jobs. Real *.concludis.de tenants (verified 2026-09-08:
                  ukr.concludis.de, swmbrk.concludis.de) are NOT -- their listing is populated by a
                  client-side jobboard widget with no static job links and no discoverable sitemap;
                  crawl_wp_jobs correctly returns 0 rows for those rather than a wrong guess. They
                  need a JS-aware adapter (not written yet) -- see crawlers/portals.py's Playwright
                  machinery for a starting point.
  wp_jobs         Same shape, used for the typo3_jobs/WordPress sites: find a job sitemap, then read
                  <title>/<h1> from each detail page. No JSON-LD on these, hence the HTML fallback.
  dvinci          d.vinci HR boards answer a public GET <host>/jobPublication/list.json (no auth, no
                  browser). The census careers_url is often a wrapper page (the clinic's own site)
                  that only embeds the real d.vinci tenant host via a jobWidgetLoader script or a
                  plain link -- resolve that host first, then hit its own list.json. Falls back to
                  scraping `var DvinciData = ({...});` off <host>/de/jobs if list.json 404s there.
  oracle          Oracle Recruiting Cloud is a JS SPA behind an XHR API; crawl_oracle tries a
                  same-origin public jobs.feed.json (schema.org DataFeed, softgarden-fronted tenants
                  like St. Josef publish this, no auth/browser needed -- reuses
                  crawlers/portals.py:parse_jobposting_feed) and falls back to crawl_wp_jobs for
                  tenants that render job links server-side instead (Klinikum FFB) or neither
                  (Altmühlfranken, still needs crawlers/portals.py's Playwright path -- not wired in).
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

from pflege_jobs import section  # noqa: E402

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


def _page_base(resp):
    """Resolve relative hrefs against a page's <base href="..."> when present (Contao and similar
    German-clinic CMSs always emit one) -- falling back to the response's own URL otherwise.
    Without this, urljoin(resp.url, relative_href) on a <base>-carrying page silently doubles up
    the path and 404s."""
    if not resp:
        return ""
    m = re.search(r'<base[^>]+href="([^"]+)"', resp.text, re.I)
    return urljoin(resp.url, _html.unescape(m.group(1))) if m else resp.url


def row(host, url, payload, vendor):
    return {"kind": "jobposting", "source_host": host, "source_url": url,
            "payload": payload, "collector": "vendor-%s-v1" % vendor, "client_id": CID}


# ---------------------------------------------------------------------------
# personio: <slug>.jobs.personio.de/xml
# ---------------------------------------------------------------------------
def personio_domain(careers_url, session=None):
    """Full <slug>.jobs.personio.(de|com) host -- some tenants (munich-airport-clinic.com) only
    ever link the .com form, and the client's own read path must be hit on that same TLD."""
    m = re.search(r"https?://([a-z0-9\-]+\.jobs\.personio\.(?:de|com))", careers_url or "", re.I)
    if m:
        return m.group(1).lower()
    r = get(careers_url, session=session)
    if r and r.ok:
        m = re.search(r"([a-z0-9\-]+\.jobs\.personio\.(?:de|com))", r.text, re.I)
        if m:
            return m.group(1).lower()
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
        # <department>/<recruitingCategory> are the tenant's own taxonomy -- free text, present on
        # some tenants and not others (section-first signal, "sometimes"; see pflege_jobs.section).
        department = f("department") or f("recruitingCategory")
        out.append({"title": name, "org": f("subcompany") or org, "office_raw": office,
                    "loc": [{"city": city, "plz": None, "region": None}],
                    "url": "%s/job/%s" % (page_url.rstrip("/").replace("/xml", ""), pid) if pid else page_url,
                    "page": page_url, "employmentType": f("employmentType"),
                    "datePosted": (f("createdAt") or "")[:10] or None,
                    "department": department,
                    "description": desc[:20000] or None})
    return out


def personio_wp_posts(careers_url, session=None):
    """Sites running the 'Personio Integration Light' WordPress plugin (ProSomno) have no
    <slug>.jobs.personio.* tenant at all -- jobs are a wp/v2/personioposition custom-post-type,
    served by WordPress's own public REST API, no auth."""
    p = urlparse(careers_url or "")
    if not p.scheme or not p.netloc:
        return None
    r = get("%s://%s/wp-json/wp/v2/personioposition?per_page=100" % (p.scheme, p.netloc), session=session)
    if not r or not r.ok:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def parse_personio_wp(posts, org):
    """The plugin's own excerpt is a single line 'Anstellungsart • Arbeitszeit • Ort' -- split on
    its own bullet separator rather than re-deriving employment type/city from the free-text body."""
    out = []
    for p in posts or []:
        title = _txt((p.get("title") or {}).get("rendered"))
        if not title:
            continue
        parts = [s.strip() for s in re.split(r"[•·]", _txt((p.get("excerpt") or {}).get("rendered")) or "") if s.strip()]
        link = p.get("link")
        out.append({"title": title, "org": org,
                    "loc": [{"city": parts[-1] if parts else None, "plz": None, "region": None}],
                    "url": link, "page": link, "employmentType": parts[0] if parts else None,
                    "datePosted": (p.get("date") or "")[:10] or None, "department": None,
                    "description": _txt((p.get("content") or {}).get("rendered"))})
    return out


def crawl_personio(c, session=None):
    """Prefer the XML feed; fall back to the WordPress plugin's own REST API, then to the site's
    own job pages.

    Some sites the census labelled "personio" only run the Personio *WordPress plugin* (ProSomno) or
    turned out not to be Personio at all (barmherzige.net). They still publish jobs under their own
    /stelle/ or /jobs/ URLs, so falling through to the generic crawler beats returning nothing.
    """
    domain = personio_domain(c.get("careers_url"), session)
    if domain:
        base = "https://%s" % domain
        r = get(base + "/xml", session=session)
        if r and r.ok:
            # the feed declares UTF-8 in its XML prolog but often ships no charset header, and
            # requests then falls back to latin-1 -> "fÃ¼r". Trust the document, not the guess.
            r.encoding = "utf-8"
            jobs = parse_personio_xml(r.text, c["name"], base + "/xml")
            if jobs:
                # office_raw/department come free in the feed already fetched -- kept as labels on
                # the row (below); no fetch-side narrowing by either one.
                for j in jobs:
                    j["section_labels"] = [j["department"]] if j.get("department") else []
                return [row(domain, j["url"], j, "personio") for j in jobs]
    posts = personio_wp_posts(c.get("careers_url"), session)
    if posts:
        jobs = parse_personio_wp(posts, c["name"])
        if jobs:
            return [row(urlparse(c["careers_url"]).netloc, j["url"], j, "personio") for j in jobs]
    return crawl_wp_jobs(c, session=session)


# ---------------------------------------------------------------------------
# smartrecruiters: api.smartrecruiters.com/v1/companies/<id>/postings
# ---------------------------------------------------------------------------
def parse_smartrecruiters(data, org, page_url):
    out = []
    for p in data.get("content") or []:
        loc = p.get("location") or {}
        dept_label = (p.get("department") or {}).get("label")
        out.append({"title": p.get("name"), "org": (p.get("company") or {}).get("name") or org,
                    "loc": [{"city": loc.get("city"), "plz": loc.get("postalCode"), "region": loc.get("region")}],
                    # postingUrl comes from the per-posting detail fetch (the real public page, with
                    # its title slug); the hand-built jobs.smartrecruiters.com/<tenant>/<id> link is
                    # only a fallback for when that detail fetch failed.
                    "url": p.get("postingUrl") or ("https://jobs.smartrecruiters.com/%s/%s" %
                           ((p.get("company") or {}).get("identifier") or "", p.get("id") or "")
                           if p.get("id") else (p.get("applyUrl") or p.get("ref"))),
                    "page": page_url, "datePosted": (p.get("releasedDate") or "")[:10] or None,
                    "employmentType": ((p.get("typeOfEmployment") or {}).get("label")),
                    "department": dept_label,
                    "section_labels": [dept_label] if dept_label else [],
                    "description": p.get("description")})
    return out


def _smartrecruiters_ident(html):
    """The tenant identifier the widget itself uses -- present directly as company_code on the
    page's own data-widget JSON (e.g. Klinik Vincentinum's /karriere/stellenangebote, which IS the
    listing page). Falls back to a jobs.smartrecruiters.com/<tenant>/<id> link, for boards that embed
    the widget on a category subpage instead (Artemed's own /karriere hub)."""
    m = re.search(r'"company_code"\s*:\s*"([A-Za-z0-9\-_]+)"', html or "")
    if m:
        return m.group(1)
    m = re.search(r"jobs\.smartrecruiters\.com/(?:ni/)?([A-Za-z0-9\-_]+)/[0-9a-f-]{8,}", html or "")
    return m.group(1) if m else None


def crawl_smartrecruiters(c, session=None):
    cu = c.get("careers_url") or ""
    ident = None
    m = re.search(r"smartrecruiters\.com/([A-Za-z0-9\-_]+)", cu)
    if m:
        ident = m.group(1)
    else:
        r = get(cu, session=session)
        if r and r.ok:
            ident = _smartrecruiters_ident(r.text)
            if not ident:
                subs = {urljoin(cu, h) for h in re.findall(r'href="([^"#?]+)"', r.text)
                        if urljoin(cu, h).startswith(cu.rstrip("/") + "/")}
                for sub in sorted(subs)[:8]:
                    rs = get(sub, session=session)
                    ident = _smartrecruiters_ident(rs.text) if rs and rs.ok else None
                    if ident: break
    if not ident:
        return crawl_wp_jobs(c, session=session)
    limit = 100
    base_url = "https://api.smartrecruiters.com/v1/companies/%s/postings" % ident

    def fetch_page(offset):
        url = "%s?limit=%d&offset=%d" % (base_url, limit, offset)
        r = get(url, session=session)
        if not r or not r.ok:
            return None, url
        try:
            return r.json(), url
        except Exception:
            return None, url

    data, first_url = fetch_page(0)
    if not data:
        return crawl_wp_jobs(c, session=session)
    content = list(data.get("content") or [])
    total_found = data.get("totalFound")

    # Section-first (surveyed 2026-09, revised): postings[].department.{id,label} is a real taxonomy
    # on this API, and &department=<id> is a confirmed working server-side filter -- but on a real
    # board (Artemed group, 438 postings) hard-narrowing the FETCH to it dropped 63 genuine
    # certified-nursing postings filed under other department buckets ("Funktionsdienst" OTA/
    # Anästhesiepflege leads, "Personal der Ausbildungsstätten" Praxisanleiter) or left untagged
    # entirely -- a real, measured coverage loss, not a hypothetical one. This API already returns
    # the full posting (title, department, ...) with no separate per-job detail fetch, so a full,
    # unfiltered walk costs the same per-page price as a filtered one: no reason to hard-filter the
    # fetch at all. Walk the whole board every time, and thread each job's own department label
    # (see parse_smartrecruiters above) into classify.classify_role's nursing_section_confirmed
    # signal downstream instead -- same taxonomy data, used as an admit signal, not a fetch filter.
    # No offset ceiling: the board's own totalFound / a short page is the only stop.
    offset = limit
    while True:
        data, url = fetch_page(offset)
        if data is None:
            break
        if total_found is None:
            total_found = data.get("totalFound")
        page = data.get("content") or []
        content.extend(page)
        if len(page) < limit or (total_found is not None and offset + limit >= total_found):
            break
        offset += limit

    if not content:
        return crawl_wp_jobs(c, session=session)

    # The list endpoint carries no description (see module docstring) -- jobAd.sections lives only on
    # the per-posting detail endpoint. Fetch it for every posting, not a sample: it is the only source
    # for description, and its postingUrl is the real slugged public page.
    for p in content:
        pid = p.get("id")
        if not pid:
            continue
        r = get("%s/%s" % (base_url, pid), session=session)
        d = None
        if r and r.ok:
            try:
                d = r.json()
            except Exception:
                d = None
        if d:
            p["postingUrl"] = d.get("postingUrl")
            secs = ((d.get("jobAd") or {}).get("sections")) or {}
            parts = [t for t in (_txt((s or {}).get("text")) for s in secs.values()) if t]
            if parts:
                p["description"] = " ".join(parts)
        time.sleep(0.5)

    out = [row("jobs.smartrecruiters.com", j["url"], j, "smartrecruiters")
           for j in parse_smartrecruiters({"content": content}, c["name"], first_url) if j.get("title") and j.get("url")]
    return out or crawl_wp_jobs(c, session=session)


# ---------------------------------------------------------------------------
# helix: <tenant>.helixjobs.com/<unit>/joblist -> /jobad?prj=<id>
# ---------------------------------------------------------------------------
def parse_helix(htmltext, base, org, page_url):
    """The joblist card wraps title AND its badge spans (schedule/contract type, e.g. "Vollzeit
    oder Teilzeit", "Festanstellung") in one anchor -- title here is a placeholder only, overwritten
    by the jobad detail's own JSON-LD title in crawl_helix (never truncated apart from the badges)."""
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
                    "url": urljoin(base, m.group(1)), "page": page_url, "description": None,
                    "datePosted": None, "employmentType": None})
    return out


def parse_helix_detail(htmltext, url, org):
    """jobad?prj= carries a schema.org JobPosting JSON-LD the joblist card lacks: real title
    (unmixed with badge text), description, jobLocation, datePosted, employmentType."""
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', htmltext or "", re.S):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        for n in (d if isinstance(d, list) else [d]):
            if not (isinstance(n, dict) and "JobPosting" in str(n.get("@type") or "")):
                continue
            a = (n.get("jobLocation") or {}).get("address") or {}
            o = n.get("hiringOrganization") or {}
            return {"title": _txt(n.get("title"), 300),
                    "org": (o.get("name") if isinstance(o, dict) else o) or org,
                    "loc": [{"city": a.get("addressLocality"), "plz": a.get("postalCode"),
                             "region": a.get("addressRegion")}],
                    "employmentType": n.get("employmentType"),
                    "datePosted": (n.get("datePosted") or "")[:10] or None,
                    "description": _txt(n.get("description"))}
    return None


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
    out = []
    for j in jobs:
        d = get(j["url"], session=session)
        detail = parse_helix_detail(d.text, j["url"], c["name"]) if d and d.ok else None
        if detail:
            j["description"], j["employmentType"], j["datePosted"] = (
                detail["description"], detail["employmentType"], detail["datePosted"])
            if detail["title"]:
                j["title"] = detail["title"]
            if detail["loc"][0]["city"]:
                j["loc"] = detail["loc"]
        if not j["loc"][0]["city"] and c.get("town"):
            j["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
        time.sleep(0.5)                              # <=2 concurrent per host, 0.5s between requests
        out.append(row(host, j["url"], j, "helix"))
    return out


# ---------------------------------------------------------------------------
# WordPress / TYPO3 "jobs" sites + concludis tenants: sitemap -> detail pages
# ---------------------------------------------------------------------------
JOB_SITEMAP = re.compile(r"(jobs?|stellen|karriere|career|vacan)", re.I)
# `stellen?\w*` covers both the German compounds the fixed alternatives missed (stellenmarkt,
# stellenboerse/stellenbörse, stellenportal) and the singular noun some boards use for a single
# posting's own path segment (TYPO3 ameosjobs: /offene-stellen/stelle/<id>-<slug>, no 'n'). `detail`
# families are how TYPO3/PERSIS boards name a single posting: /detail/j/<slug>-<id>.html,
# /karriere-detail/<city>/<slug>/<id>, /detail/<uuid>/.
# TYPO3's softgarden connector extension (e.g. hessing-kliniken.de) puts the job id in the query
# string, not the path -- /karriere/detail/?tx_softgarden_jobliste[job]=53551366 -- so the path-only
# alternatives above never match it; add the query-string shape as its own alternative.
JOB_PATH = re.compile(r"/(jobs?|stellen?\w*|karriere/stellen|vacan)[/-]|/(karriere-)?detail/[^/?#]"
                       r"|tx_\w*jobliste%5[Bb]job%5[Dd]=\d+", re.I)
# A job-alert subscribe widget's own path ("/job-newsletter", concludis' "/jobletter") matches
# JOB_PATH on "job[/-]" alone -- it is a write-only email-signup page, never a posting (confirmed
# live: karriere.ameos.eu's own "/job-newsletter" parsed as a fake "Job-Newsletter" row).
NOT_JOB_PATH = re.compile(r"/job-?(?:news)?letter\b", re.I)
# Loose fallback: a detail URL directly under a job-ish path segment (e.g. Wix's bare
# /karriere/<slug>), only tried against locs pulled from a sitemap whose own URL already
# matched JOB_SITEMAP (so it's not applied to a site's whole, unfiltered sitemap).
JOB_PATH_LOOSE = re.compile(r"/(jobs?|stellen?|stellenangebote?|stellenanzeigen?|karriere|vacan)/[^/]+$", re.I)
# TYPO3 "klinikumbasics/klinikumjobs" widget (München Klinik and sibling city-clinic sites): every
# division/category page under /jobs/<division>/ renders an empty <ul class="job-results"> -- the
# real postings sit only in this inline JSON var, client-filtered by JS. Its own <h1> is the
# division's marketing headline ("HEILEN KÖNNEN."), never a job title -- see _wp_job_rows.
ALLJOBS_RX = re.compile(r"var\s+allJobs\s*=\s*(\[.*?\])\s*;", re.S)


def find_job_urls(base, session=None, max_maps=8):
    """Follow robots.txt + sitemap indexes, prefer a jobs-specific sitemap, return job detail URLs."""
    maps, out, job_out = [], [], []
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
            if JOB_SITEMAP.search(u):
                job_out += locs
    found = [u for u in dict.fromkeys(out) if JOB_PATH.search(u) and not NOT_JOB_PATH.search(u)]
    if not found and job_out:
        found = [u for u in dict.fromkeys(job_out) if JOB_PATH_LOOSE.search(u)]
    if not found:
        # Silent zero-yield here is indistinguishable from "board has no jobs right now" --
        # but it is usually a JS-only site (sitemap has no job links) or an unmatched URL shape.
        # Surface it in the run log so these boards are visible instead of vanishing quietly.
        print("[wp_jobs] find_job_urls: no job links in sitemap for %s (%d sitemap urls seen)"
              % (base, len(out)), file=sys.stderr)
    return found


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
                        "employmentType": n.get("employmentType"),
                        "description": _txt(n.get("description"))}
            stack += [v for v in n.values() if isinstance(v, (dict, list))]
            stack += [x for v in n.values() if isinstance(v, list) for x in v if isinstance(x, dict)]
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", htmltext or "", re.S)
    # A site-wide a11y label (e.g. <h1 class="visuallyhidden">Klinikum X gGmbH</h1>) sits on every
    # page including the shell of a JS-rendered detail page -- never a job title, so treat it as no
    # h1 at all rather than let it win by default.
    a11y_h1 = bool(h1 and re.search(r'class="[^"]*\b(visuallyhidden|visually-hidden|sr-only|screen-reader-text)\b',
                                     h1.group(0), re.I))
    if a11y_h1:
        h1 = None
    title = _txt(h1.group(1), 300) if h1 else None
    if title:
        title = re.sub(r"^(Bewirb dich als|Jetzt bewerben als|Stellenangebot:?)\s+", "", title, flags=re.I)
    if not title or not GENDER.search(title):
        t = re.search(r"<title>(.*?)</title>", htmltext or "", re.S)
        cand = _txt(t.group(1), 300) if t else None
        if cand:
            # Usually "<real title> | SiteName", segment 0 -- but a HubSpot-templated board
            # (confirmed live: meinkrankenhaus2030.de) instead writes "Stellenanzeige | <real
            # title>", the generic label first. Prefer whichever segment actually carries a
            # gender marker; only default to segment 0 when neither (or only one) does.
            segs = [s.strip() for s in re.split(r"\s+[–—|]\s+", cand) if s.strip()]
            cand = next((s for s in segs if GENDER.search(s)), segs[0] if segs else cand)
            if GENDER.search(cand) or not title:
                title = cand
    if not title or not GENDER.search(title):
        # Neither h1 nor <title> carries a gender marker (bespoke CMS keeps the real title in a
        # plain <h2>/<h3> instead, e.g. KWM) -- take the first heading that does, else give up
        # rather than return a fake title (a11y h1 above, or a generic <title>).
        for hm in re.finditer(r"<h[23][^>]*>(.*?)</h[23]>", htmltext or "", re.S):
            cand = _txt(hm.group(1), 300)
            if cand and GENDER.search(cand):
                title = cand
                break
        else:
            # Suppressed a11y h1 and no gender-marked heading anywhere (Initiativbewerbung/
            # Blitzbewerbung shell) -- the only title left is the generic <title> tag, not a real
            # posting; drop it instead of ingesting a fake row.
            if a11y_h1:
                return None
    if not title:
        return None
    body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", htmltext or "")
    # No JSON-LD JobPosting anywhere (confirmed live: karriere.barmherzige.net) -- but the page still
    # states employmentType/city plainly next to a schedule/location icon; read that instead of
    # leaving fields the source does expose empty.
    facts = dict(re.findall(r'icons/([a-z]+)\.svg"[^>]*/?>\s*<span class="fact">([^<]+)</span>',
                             htmltext or "", re.I))
    return {"title": title, "org": org,
            "loc": [{"city": facts.get("location"), "plz": None, "region": None}],
            "url": url, "page": url, "description": _txt(body),
            "employmentType": facts.get("schedule")}


def _wp_job_rows(urls, c, host, max_jobs, session, section_labels=None, seen=None, titles=None):
    """Fetch each url and turn it into a row -- except a klinikum-jobs widget page (ALLJOBS_RX),
    whose own title is never a job (see the regex's docstring): walk its embedded postings' own
    `link`s instead of accepting the division page itself as one fake row. `titles` (url -> anchor
    text, from the discovery step) backs a PDF-linked posting: its response body has no HTML to
    read a title from at all, so parse_job_page would otherwise just drop it."""
    seen = seen if seen is not None else set()
    titles = titles or {}
    out = []
    for u in urls:
        key = _listing_page_key(u)
        if len(out) >= max_jobs or key in seen:
            continue
        seen.add(key)
        if PDF_LINK_RX.search(u):
            title = titles.get(u)
            if not title:
                continue
            j = {"title": title, "org": c["name"], "loc": [{"city": c.get("town"), "plz": None, "region": None}],
                 "url": u, "page": u, "description": None, "employmentType": None}
            j["section_labels"] = list(section_labels) if section_labels else []
            out.append(row(host, u, j, "wp_jobs"))
            continue
        r = get(u, session=session)
        if not r or not r.ok:
            continue
        final_key = _listing_page_key(r.url)
        if final_key in seen and final_key != key:
            # A stale/expired job slug that 200s instead of 404ing (seen live: medbo.de) redirects
            # to one shared generic landing page instead -- a second, different, candidate slug
            # landing on a final url another candidate already claimed is that catch-all, not a
            # second distinct posting.
            continue
        seen.add(final_key)
        m = ALLJOBS_RX.search(r.text)
        if m:
            try:
                entries = json.loads(m.group(1))
            except Exception:
                entries = []
            widget_urls = [urljoin(r.url, e["link"]) for e in entries if e.get("link")]
            out += _wp_job_rows(widget_urls, c, host, max_jobs - len(out), session, section_labels, seen)
            continue
        j = parse_job_page(r.text, r.url, c["name"])
        if not j or not j.get("title"):
            continue
        if not j["loc"][0]["city"] and c.get("town"):
            j["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
        j["section_labels"] = list(section_labels) if section_labels else []
        out.append(row(host, j["url"], j, "wp_jobs"))
        time.sleep(0.2)
    return out


def _wp_nursing_section_url(cu, cu_resp):
    """Look for a Berufsgruppe-style nav <select>/<a> on the career page naming a nursing
    department, and return (label, absolute_url) -- or (None, None) if no such nav exists on this
    board at all (checked, not assumed) or none of its options/links name nursing."""
    if not cu_resp or not cu_resp.ok:
        return None, None
    base = _page_base(cu_resp)
    links = []
    for h, label in re.findall(r'<option[^>]+data-url="([^"]+)"[^>]*>([^<]*)</option>', cu_resp.text, re.I):
        links.append((_txt(label), urljoin(base, _html.unescape(h))))
    for h, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>([^<]*)</a>', cu_resp.text, re.I):
        label = _txt(label)
        # A genuine Berufsgruppe/category nav label is a short word or two ("Pflege",
        # "Pflegedienst"); a full job-posting title that merely happens to mention nursing
        # ("Examinierte Pflegefachkraft (w/m/d) fuer unsere Notaufnahme, stellv. Stationsleitung")
        # is not a category link, and following it would "narrow" to one job's own detail/related
        # links instead of the section.
        if label and len(label) <= 40:
            links.append((label, urljoin(base, _html.unescape(h))))
    href = section.pick_nursing_link(links)
    if href and href.rstrip("/") == cu.rstrip("/"):
        return None, None
    if not href:
        return None, None
    label = next((t for t, h in links if h == href), None)
    return label, href


# A small clinic often skips a real detail page entirely and links a PDF flyer straight off the
# listing page (confirmed live: augenklinik-muenchen.de, 3 "..._v02.pdf" attachments, no HTML detail
# anywhere) -- the anchor's own visible text is the only real title available (a PDF response body
# has no <h1>/<title> parse_job_page can read), so every job-link scan below captures it alongside
# the href, not just the href.
PDF_LINK_RX = re.compile(r"\.pdf(?:[?#]|$)", re.I)


def _job_link_pairs(html, base, exclude=()):
    """(url, anchor text) for every JOB_PATH-matching link on one already-fetched page. `.*?` (not
    `[^<]*`) for the text span: a job title often sits inside a nested <span itemprop="title"> (seen
    live: karriere.ameos.eu's own schema.org microdata markup) -- requiring tag-free anchor content
    silently dropped every one of its links pre-fix. _txt() strips whatever tags are inside."""
    out = {}
    for h, t in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S):
        if not JOB_PATH.search(h) or NOT_JOB_PATH.search(h):
            continue
        u = urljoin(base, _html.unescape(h))
        if u in exclude or u in out:
            continue
        out[u] = _txt(t)
    return out


def _page_job_links(url, session=None, exclude=()):
    """Job-looking links on one page, minus `exclude` -- the site-wide nav/footer (Berufsgruppe
    siblings, "Karriere"/"Impressum"/...) repeats on every subpage, so without excluding whatever
    already linked from the entry page, a "narrower" section page can resurface the whole board."""
    r = get(url, session=session)
    if not r or not r.ok:
        return []
    base = _page_base(r)
    html = re.sub(r"(?s)<!--.*?-->", "", r.text)
    return list(_job_link_pairs(html, base, exclude))


# Server-rendered pager (TYPO3 ameosjobs and similar bespoke listing tables): an anchor whose own
# text is "nächste"/"next"/"weiter"/"»" keeps pointing at the next page -- following it is the only
# way to see a table beyond page 1 (confirmed live: karriere.ameos.eu declares 765 postings, page 1
# alone lists 10).
# href="([^"]+)" (not [^"#]+) -- a next-page link's own query string can legitimately carry a
# same-page anchor after it (ameosjobs: "...&page]=2&cHash=...#jobs-listing"); excluding '#' from
# the class made the whole href-then-closing-quote match impossible, so the pattern never fired.
NEXT_PAGE_RX = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>\s*(?:nächste|next|weiter|»)\s*</a>', re.I)
# TYPO3 Solr search widgets (concludis-fronted "mama-search" web component, martha-maria group)
# instead embed the pager as a JSON `"next":"..."` field inside an inline <script> (TYPO3.settings.TS
# xhrCache), never as a plain anchor -- confirmed live: karriere.martha-maria.de declares 60
# postings, the visible page 1 lists 10, and the widget's own `result-url`/`suggest-url` attributes
# name the same tx_solr endpoint this JSON pager walks.
NEXT_PAGE_JSON_RX = re.compile(r'"next"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _next_page_url(html, base):
    m = NEXT_PAGE_RX.search(html)
    if m:
        return urljoin(base, _html.unescape(m.group(1)))
    m = NEXT_PAGE_JSON_RX.search(html)
    if m and m.group(1):
        return urljoin(base, m.group(1).replace("\\/", "/"))
    return None


def _paginated_job_links(start_url, session=None, exclude=(), first_resp=None, max_pages=200, titles=None):
    """Job-looking links across a server-rendered listing's own pagination, walking its "next page"
    link (NEXT_PAGE_RX) until none remains or a page adds nothing new. `first_resp` reuses an
    already-fetched page 1 (crawl_wp_jobs already fetched `cu`) instead of re-fetching it. 200 pages
    is a loop-safety ceiling, not a board-size cap -- no real board is near it. `titles`, if given, is
    filled in-place with each link's own anchor text (url -> text) -- the only real title a PDF-linked
    posting has, see PDF_LINK_RX."""
    out, visited, url, resp = [], set(), start_url, first_resp
    for _ in range(max_pages):
        if not url or url in visited:
            break
        visited.add(url)
        r = resp if resp is not None else get(url, session=session)
        resp = None
        if not r or not r.ok:
            break
        base = _page_base(r)
        html = re.sub(r"(?s)<!--.*?-->", "", r.text)
        pairs = _job_link_pairs(html, base, exclude=exclude)
        if titles is not None:
            for u, t in pairs.items():
                titles.setdefault(u, t)
        fresh = [u for u in pairs if u not in out]
        out += fresh
        nxt = _next_page_url(html, base)
        if not fresh and not nxt:
            break
        url = nxt
        time.sleep(0.3)
    return out


# A job-filter/listing widget (medbo's TYPO3 cn_medbo_jobs extension) advertises its own AJAX read
# path as a `data-url` attribute on the career page itself -- the board's own client calls it (seen
# live: a job-shaped `?tx_..._joblist[action]=ajaxFilter...` url), so crawl_wp_jobs must too, even
# when every posting is already reachable through friendlier detail-page links found elsewhere.
DATA_URL_JOB_RX = re.compile(r'data-url="([^"]*(?:job|stellen)[^"]*)"', re.I)


def _widget_endpoint_job_links(cu_resp, session=None):
    if not cu_resp or not cu_resp.ok:
        return []
    out = []
    for h in dict.fromkeys(DATA_URL_JOB_RX.findall(cu_resp.text)):
        u = urljoin(cu_resp.url, _html.unescape(h))
        r = get(u, session=session)
        if not r or not r.ok:
            continue
        try:
            content = r.json().get("content") or ""
        except Exception:
            content = r.text
        out += [urljoin(r.url, hh) for hh in re.findall(r'href="([^"#]+)"', content)]
    return list(dict.fromkeys(out))


def _listing_page_key(u):
    """Normalize a URL for "is this the listing page itself" comparisons -- strip index.php/.html
    and a trailing slash so http://x/stellenangebote/ == http://x/stellenangebote/index.php."""
    p = urlparse(u)
    path = re.sub(r"/index\.(php|html?)$", "/", p.path or "/", flags=re.I).rstrip("/") or "/"
    return (p.netloc.lower(), path.lower())


def _faqpage_job_rows(cu_resp, c, host):
    """Some small-clinic WordPress sites (confirmed live: klinik-steger.de, the "Ultimate Addons for
    Gutenberg" FAQ block) render their whole job listing as one FAQPage JSON-LD block instead of a
    JobPosting -- each posting is a Question (title) + Answer (description), inline on the listing
    page itself, no per-job href to walk at all. parse_job_page never sees this (it only recognizes
    @type JobPosting) and there is no detail link for the generic href/sitemap scan to find either --
    a dedicated, narrow read of this one schema shape."""
    if not cu_resp or not cu_resp.ok:
        return []
    out = []
    for m in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', cu_resp.text, re.S):
        try:
            d = json.loads(m)
        except Exception:
            continue
        for node in (d if isinstance(d, list) else [d]):
            if not isinstance(node, dict) or "FAQPage" not in str(node.get("@type") or ""):
                continue
            for q in node.get("mainEntity") or []:
                title = _txt(q.get("name"))
                if not title:
                    continue
                answer = (q.get("acceptedAnswer") or {}).get("text")
                j = {"title": title, "org": c["name"],
                     "loc": [{"city": c.get("town"), "plz": None, "region": None}],
                     "url": cu_resp.url, "page": cu_resp.url, "description": _txt(answer), "employmentType": None}
                out.append(row(host, cu_resp.url, j, "wp_jobs"))
    return out


# A second, unrelated small-clinic "job FAQ accordion" theme (confirmed live: waldhausklinik.de) --
# no JSON-LD at all here, plain HTML with descriptive class names. Every posting is one
# <div class="faqAccCard">, its title in .jobHeadmain, publish date next to "Veröffentlicht am:",
# description in the matching .faqAccCardBody.
FAQ_CARD_RX = re.compile(r'<div class="faqAccCard">(.*?)(?=<div class="faqAccCard">|$)', re.S)
FAQ_TITLE_RX = re.compile(r'class="jobHeadmain">\s*(.*?)\s*<div', re.S)
FAQ_BODY_RX = re.compile(r'class="faqAccCardBody">(.*?)</div>\s*</div>\s*</div>', re.S)
FAQ_DATE_RX = re.compile(r'Ver\xf6ffentlicht am:.*?<b>\s*(\d{1,2})\.?\s*([A-Za-zä]+)\.?\s*(\d{4})', re.S)
DE_MONTHS = {"januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
             "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12}


def _faq_accordion_job_rows(cu_resp, c, host):
    if not cu_resp or not cu_resp.ok or 'class="faqAccCard"' not in cu_resp.text:
        return []
    out = []
    for card in FAQ_CARD_RX.findall(cu_resp.text):
        tm = FAQ_TITLE_RX.search(card)
        title = _txt(tm.group(1)) if tm else None
        if not title:
            continue
        bm = FAQ_BODY_RX.search(card)
        dm = FAQ_DATE_RX.search(card)
        date_posted = None
        if dm:
            month = DE_MONTHS.get(dm.group(2).lower())
            if month:
                date_posted = f"{dm.group(3)}-{month:02d}-{int(dm.group(1)):02d}"
        j = {"title": title, "org": c["name"],
             "loc": [{"city": c.get("town"), "plz": None, "region": None}],
             "url": cu_resp.url, "page": cu_resp.url, "description": _txt(bm.group(1)) if bm else None,
             "employmentType": None, "datePosted": date_posted}
        out.append(row(host, cu_resp.url, j, "wp_jobs"))
    return out


def crawl_wp_jobs(c, session=None, max_jobs=100_000):  # loop-safety ceiling, not a board-size cap
    cu = (c.get("careers_url") or "").strip()
    if not cu:
        return []
    p = urlparse(cu)
    base = "%s://%s" % (p.scheme, p.netloc)
    host = p.netloc
    # A JS-rendered listing page (concludis widgets etc.) can end up as its own only "job" link when
    # nothing else on the static HTML matches JOB_PATH; never let the listing page masquerade as a
    # posting -- parse_job_page would just re-extract the listing's own <h1> as a fake job title.
    not_a_job = {_listing_page_key(cu)}

    # Section-first (surveyed 2026-09, revised): some bespoke career pages nav to a distinct,
    # already-narrowed nursing listing (a "Berufsgruppe" select or footer link) -- fetching it first
    # beats walking the whole board cold. This used to STOP there when it yielded any rows -- but on
    # a real board (Klinikum Nürnberg, 120 postings) that meant the full sitemap walk never even ran,
    # silently missing 2 genuine certified-nursing postings filed under a different "Jobwelt"
    # ("OTA / Pflegefachkraft OP (m/w/d) Zentral-OP", "ATA / Pflegefachkraft Anästhesie (m/w/d)
    # Zentral-OP") whose own label never named nursing at all. Still fetch the confirmed section
    # first (so it's never skipped for a partial board budget), but then top up with the rest of the
    # board's job urls (deduped, budget-capped) instead of returning early -- this vendor's adapter
    # has no cheap per-job label at fetch time to gate on the way career_crawl/bite do, so the only
    # way to recover a mislabelled posting is to fetch it.
    cu_resp = get(cu, session=session)
    if cu_resp and cu_resp.ok:
        not_a_job.add(_listing_page_key(cu_resp.url))
        # beesite (muz global-jobboard-client): fingerprinted on the page already fetched here, no
        # extra request -- its own listing is JS-injected, so the walk below would find nothing.
        # Never a registry label (see pflege_jobs.sources.beesite docstring, TASK-40 AC#3).
        from pflege_jobs.sources.beesite import is_beesite, crawl_beesite
        if is_beesite(cu_resp):
            return crawl_beesite(c, session=session)
        faq_rows = _faqpage_job_rows(cu_resp, c, host) or _faq_accordion_job_rows(cu_resp, c, host)
        if faq_rows:
            return _enrich_wp_fallback_fields(faq_rows, session=session)
    section_label, section_url = _wp_nursing_section_url(cu, cu_resp)
    # One `seen` set shared across every _wp_job_rows call below (section, sitemap, career-page-link
    # stages) -- without it each stage's own fresh dedup set can't see a page (or a klinikum-jobs
    # widget page's own embedded postings, ALLJOBS_RX) another stage already fetched, and the same
    # job comes back as a duplicate row once per stage that happens to reach it.
    out, fetched, seen, titles = [], set(), set(), {}
    # Seed `titles` from the career page itself before the sitemap stage runs (not just from the
    # career-page-link stage below, which runs last): a PDF-linked posting's title only exists as
    # anchor text on a page that links it, never inside the PDF response `_wp_job_rows` would
    # otherwise fetch -- if the sitemap discovers that same PDF url first with no title available
    # yet, `seen` permanently drops it before the later stage ever gets a chance to supply one
    # (confirmed live: augenklinik-muenchen.de, one of 3 PDFs lost this way pre-fix).
    if cu_resp and cu_resp.ok:
        titles.update(_job_link_pairs(re.sub(r"(?s)<!--.*?-->", "", cu_resp.text), _page_base(cu_resp)))
    if section_url:
        cu_links = ({urljoin(_page_base(cu_resp), h) for h in re.findall(r'href="([^"#]+)"', cu_resp.text)}
                    if cu_resp and cu_resp.ok else set())
        section_urls = [u for u in _paginated_job_links(section_url, session=session, exclude=cu_links | {section_url}, titles=titles)
                        if _listing_page_key(u) not in not_a_job]
        if section_urls:
            out = _wp_job_rows(section_urls, c, host, max_jobs, session,
                               section_labels=[section_label] if section_label else None, seen=seen, titles=titles)
            fetched = {j["payload"]["url"] for j in out}

    urls = [u for u in find_job_urls(base, session=session) if _listing_page_key(u) not in not_a_job]
    remaining = max(max_jobs - len(out), 0)
    if urls and remaining:
        more_urls = [u for u in urls if u not in fetched]
        new = _wp_job_rows(more_urls, c, host, remaining, session, seen=seen, titles=titles)
        out += new
        fetched |= {j["payload"]["url"] for j in new}

    # Always also walk the career page's own job-shaped links, not just when the two paths above
    # found nothing: a client-side "klinikum-jobs" division page (ALLJOBS_RX) exposes real postings
    # via an embedded widget the sitemap crawl above can miss (it's paged/capped, not a full site
    # index), and the career page's own nav links straight to every such division page.
    # _paginated_job_links reuses the already-fetched `cu_resp` as page 1, then follows the career
    # page's own "next page" link -- a table paginated on the career page itself (ameosjobs), not
    # just a distinct narrower section, would otherwise only ever be read one page deep.
    page_urls = _paginated_job_links(cu, session=session, first_resp=cu_resp, titles=titles) if cu_resp and cu_resp.ok else []
    page_urls += _widget_endpoint_job_links(cu_resp, session=session)
    page_urls = [u for u in dict.fromkeys(page_urls) if _listing_page_key(u) not in not_a_job and u not in fetched]
    remaining = max(max_jobs - len(out), 0)
    if page_urls and remaining:
        new = _wp_job_rows(page_urls, c, host, remaining, session, seen=seen, titles=titles)
        out += new
        fetched |= {j["payload"]["url"] for j in new}

    if not out:
        # hr4you: the career page names no vendor markup at all (its tenant links sit on a
        # location/jobs subpage the page itself points to, not on the page or its own scripts) --
        # tried last, after every generic sitemap/page-link path above found nothing. Never a
        # registry label either (see pflege_jobs.sources.hr4you docstring, TASK-40 AC#3).
        from pflege_jobs.sources.hr4you import crawl_hr4you
        out = crawl_hr4you(c, session=session)
    # crawl_wp_jobs's own parser extracts no employmentType/datePosted from plain HTML (only from
    # JSON-LD, where present) -- backfill both from what these WordPress/TYPO3 boards already
    # publish (Vollzeit/Teilzeit in the body, WP SEO meta dates), not just for crawl_oracle's fallback.
    return _enrich_wp_fallback_fields(out, session=session)


WP_META_DATE_RX = re.compile(r'(?:property="article:modified_time"|property="og:updated_time"'
                              r'|property="article:published_time")\s+content="([^"]+)"')
# InnKlinikum (non-WordPress, no SEO-plugin meta at all) instead labels its own posting window in the
# body: "Interne Ausschreibung: vom DD.MM.YYYY bis DD.MM.YYYY" -- the "vom" (from) date is this
# posting's own publish date, not a guess (verified against 14/15 sampled detail pages).
AUSSCHREIBUNG_DATE_RX = re.compile(r"Ausschreibung:\s*</div>\s*<div[^>]*>\s*vom\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", re.S)
EMPLOYMENT_KEYWORD_RX = re.compile(r"\b(Vollzeit|Teilzeit|Minijob)\b", re.I)
# schema.org JobPosting as bare itemprop microdata, no JSON-LD at all (parse_job_page's JSON-LD
# branch never sees it): TYPO3 ameosjobs detail pages carry <meta itemprop="datePosted"
# content="YYYY-MM-DD"> the same way mein-check-in's own detail pages do (see MCI_ITEMPROP) --
# confirmed live (karriere.ameos.eu): 0/766 rows had a date before this, despite every detail page
# already publishing one.
ITEMPROP_DATE_RX = re.compile(r'itemprop="datePosted"[^>]*content="([^"]+)"')


def _enrich_wp_fallback_fields(rows, session=None):
    """crawl_wp_jobs's own parser (owned by TASK-35) extracts no employmentType/datePosted --
    backfill both here from data these WordPress boards already publish: Vollzeit/Teilzeit named in
    the visible body (already sitting in payload.description, no extra fetch), the
    article:modified_time / og:updated_time <meta> every WP SEO plugin (Yoast, RankMath) stamps on
    each post, and a bare schema.org itemprop="datePosted" meta some TYPO3 boards use instead of
    JSON-LD (one light re-fetch per row, since crawl_wp_jobs keeps only the body text, not <head>).
    Verified stable, not request-time-generated, by refetching the same detail page twice."""
    for r in rows:
        p = r["payload"]
        if not p.get("employmentType"):
            m = EMPLOYMENT_KEYWORD_RX.search(p.get("description") or "")
            if m:
                p["employmentType"] = m.group(1).title()
        if not p.get("datePosted"):
            resp = get(p.get("url") or r["source_url"], session=session)
            if resp and resp.ok:
                dm = WP_META_DATE_RX.search(resp.text)
                am = AUSSCHREIBUNG_DATE_RX.search(resp.text)
                im = ITEMPROP_DATE_RX.search(resp.text)
                if dm:
                    p["datePosted"] = dm.group(1)[:10]
                elif am:
                    p["datePosted"] = "%s-%02d-%02d" % (am.group(3), int(am.group(2)), int(am.group(1)))
                elif im:
                    p["datePosted"] = im.group(1)[:10]
            time.sleep(0.5)
    return rows


def crawl_oracle(c, session=None):
    """Oracle Recruiting Cloud is a client-rendered SPA -- no server-rendered job links to walk a
    sitemap for. Some tenants (softgarden-fronted, e.g. karriere.josef.de) also publish a public,
    unauthenticated schema.org DataFeed at <origin>/jobs.feed.json; prefer that when present, since
    it has full descriptions and needs no browser. Falls back to crawl_wp_jobs for tenants that
    render job links server-side instead (Klinikum FFB, Altmühlfranken); that fallback's own rows
    carry no employmentType/datePosted, so _enrich_wp_fallback_fields backfills both."""
    cu = (c.get("careers_url") or "").strip()
    if cu:
        p = urlparse(cu)
        feed_url = "%s://%s/jobs.feed.json" % (p.scheme, p.netloc)
        r = get(feed_url, session=session)
        if r and r.ok:
            try:
                data = r.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("dataFeedElement"):
                from crawlers.portals import parse_jobposting_feed
                jobs = parse_jobposting_feed(data, feed_url)
                if jobs:
                    return [row(p.netloc, j["url"], j, "oracle") for j in jobs if j.get("title") and j.get("url")]
    return _enrich_wp_fallback_fields(crawl_wp_jobs(c, session=session), session=session)


REXX_JOB_HREF = re.compile(r'href="([^"#]*-j\d+\.html[^"]*)"')
REXX_TAG = re.compile(r'<span[^>]*class="[^"]*job_details_first[^"]*"[^>]*>(.*?)</span>', re.S)
# parse_job_page's JSON-LD branch (shared by every family on this file) takes title/org/loc/dates/
# description but not employmentType -- rexx's own JobPosting JSON-LD carries it plainly
# ("FULL_TIME"/"PART_TIME"), already sitting in the detail page just fetched, so read it here
# rather than widen a helper other adapters also depend on.
REXX_EMPLOYMENT_TYPE_RX = re.compile(r'"employmentType"\s*:\s*"([^"]+)"')


def _rexx_listing_jobs(htmltext):
    """One listing page -> [(href, Fachbereich_tag_or_None)], one per job container. The tag is a
    free label already sitting next to the title in the same fetch -- no extra request needed."""
    # The container element's own tag varies by skin (<div>, <article>, ...) -- split on the class
    # regardless of which tag carries it.
    chunks = re.split(r'(?=<[a-zA-Z]+[^>]*\bclass="[^"]*\bjoboffer_container\b)', htmltext or "")
    out = []
    for chunk in chunks:
        hm = REXX_JOB_HREF.search(chunk)
        if not hm:
            continue
        tm = REXX_TAG.search(chunk)
        out.append((hm.group(1), _txt(tm.group(1)) if tm else None))
    return out


def crawl_rexx(c, session=None):
    """rexx systems: a server-rendered listing whose job links end in `-<lang>-j<id>.html`.

    No JSON/feed endpoint is exposed (api/, f=json both 404), but the listing needs no JavaScript,
    so a plain fetch + one request per detail page is enough. The `-j<id>` suffix is the stable
    identifier; the rest of the slug is the (mutable) title, so dedupe on it. No cap on job count --
    the board's own end signal (a page with zero new ids) is the only stop (a real board measured at
    330 postings used to be silently truncated to 300 by a VENDOR_MAX_JOBS default here).
    """
    cu = (c.get("careers_url") or "").strip()
    if not cu:
        return []
    # The listing shows 100 jobs at a time and pages with ?start=N (no visible pager on some skins),
    # so a single fetch silently truncates the biggest boards at exactly 100. Page until no new ids --
    # 1000 pages (100k jobs) is a loop-safety ceiling, not a board-size cap; no real board is near it.
    urls, tags, seen, base = [], {}, set(), None
    for start in range(0, 100000, 100):
        page_url = cu if start == 0 else cu + ("&" if "?" in cu else "?") + "start=%d" % start
        r = get(page_url, session=session)
        if not r or not r.ok:
            break
        if base is None:
            base = "%s://%s" % (urlparse(r.url).scheme, urlparse(r.url).netloc)
        fresh = 0
        for h, tag in _rexx_listing_jobs(r.text):
            u = urljoin(r.url, _html.unescape(h))
            jid = re.search(r"-j(\d+)\.html", u)
            if not jid or jid.group(1) in seen:
                continue
            seen.add(jid.group(1))
            urls.append(u)
            if tag:
                tags[u] = tag
            fresh += 1
        if fresh == 0:
            break
        time.sleep(0.2)
    if base is None:
        return []
    # Every skin's own template links a plain, query-free /stellenangebote.html regardless of which
    # variant (bare host, or a filtered ?search_mode=... query) `cu` itself is -- when `cu` isn't
    # already that exact path, the client's page names it as a read path our own start=N walk above
    # (which only ever varies `cu`'s own query) never visits. Same board, same jobs (verified: identical
    # ids), so this costs one request and adds no new postings -- it only closes the coverage gap.
    canonical = base + "/stellenangebote.html"
    if cu.rstrip("/") != canonical:
        r = get(canonical, session=session)
        if r and r.ok:
            for h, tag in _rexx_listing_jobs(r.text):
                u = urljoin(r.url, _html.unescape(h))
                jid = re.search(r"-j(\d+)\.html", u)
                if not jid or jid.group(1) in seen:
                    continue
                seen.add(jid.group(1))
                urls.append(u)
                if tag:
                    tags[u] = tag

    # Section-first (surveyed 2026-09, revised): the listing already tags every job with a
    # Fachbereich label (no extra request). This used to also HARD-FILTER which detail pages got
    # fetched at all -- but on a real board (Schön Klinik group, 296 postings) that dropped 30
    # genuine certified-nursing postings filed under a different Fachbereich than the one nursing
    # bucket ("OP Pfleger oder Operationstechnischer Assistent", "MFA/MTRA/Pflegefachkraft
    # Herzkatheterlabor..."), a real, measured coverage loss -- fetch every url, and thread each job's
    # own Fachbereich tag (already sitting in `tags`, no extra request) into the payload for
    # classify.classify_role's nursing_section_confirmed signal downstream instead of using it as a
    # fetch filter.
    out = []
    for u in urls:
        d = get(u, session=session)
        if not d or not d.ok:
            continue
        j = parse_job_page(d.text, d.url, c["name"])
        if not j or not j.get("title"):
            continue
        if not j["loc"][0]["city"] and c.get("town"):
            j["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
        j["section_labels"] = [tags[u]] if tags.get(u) else []
        em = REXX_EMPLOYMENT_TYPE_RX.search(d.text)
        if em:
            j["employmentType"] = em.group(1)
        out.append(row(urlparse(base).netloc, j["url"], j, "rexx"))
        time.sleep(0.2)
    return out


# mein-check-in.de hosts one tenant per employer; the careers page just links into it.
MCI_TENANT = re.compile(r"mein-check-in\.de/([a-z0-9][a-z0-9_-]*)/?", re.I)

# Every detail page carries a schema.org JobPosting as microdata spans (not JSON-LD, so
# parse_job_page's JSON-LD-only reader never sees it) -- datePosted/employmentType and the
# per-job address live only here, one regex per itemprop.
MCI_ITEMPROP = {name: re.compile(r'itemprop="%s"[^>]*>([^<]*)' % name)
                 for name in ("datePosted", "employmentType", "addressLocality", "postalCode", "addressRegion")}


def _mci_job_meta(html):
    return {name: (rx.search(html).group(1).strip() or None) if rx.search(html) else None
            for name, rx in MCI_ITEMPROP.items()}


def crawl_mein_check_in(c, session=None, max_jobs=int(os.environ.get("VENDOR_MAX_JOBS", "300"))):
    """mein-check-in: resolve the tenant slug, then read /<tenant>/overview.

    The clinic's own careers page is usually a thin wrapper that links to
    `mein-check-in.de/<tenant>/index`; the listing lives on that host and is server-rendered, with
    each vacancy as `position-<id>`. Titles are already in the listing anchors, so the detail fetch
    is only needed for the description plus the JobPosting microdata (datePosted, employmentType,
    per-job address) -- keeping the crawl to ~1 request per job.
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

    # The overview page groups every position under a sidebar heading (<li id="pg-<id>">
    # <span>Pflegedienst</span><ul>...position-<id> links...</ul></li>) -- read that label onto
    # each position as data (section_labels below), no extra request needed.
    pid_group = {}
    groups = re.findall(r'<li id="pg-\d+"[^>]*>\s*<span[^>]*>(.*?)</span>(.*?)(?=<li id="pg-|\Z)', listing.text, re.S)
    for label, body in groups:
        lbl = _txt(label)
        for pid in re.findall(r'position-(\d+)', body):
            pid_group.setdefault(pid, lbl)

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
             "url": u, "page": u, "description": None,
             "section_labels": [pid_group[pid]] if pid_group.get(pid) else []}
        d = get(u, session=session)
        if d and d.ok:
            full = parse_job_page(d.text, u, c["name"])
            if full and full.get("description"):
                j["description"] = full["description"]
            meta = _mci_job_meta(d.text)
            j["datePosted"] = (meta["datePosted"] or "")[:10] or None
            j["employmentType"] = meta["employmentType"]
            if meta["addressLocality"]:                   # source gives the real per-job branch
                j["loc"] = [{"city": meta["addressLocality"], "plz": meta["postalCode"],
                             "region": meta["addressRegion"] or "BAYERN"}]
        out.append(row(host, u, j, "mein-check-in"))
        if len(out) >= max_jobs:
            break
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------------------
# d.vinci: <host>/jobPublication/list.json
# ---------------------------------------------------------------------------
# careers_url on the census is often the clinic's own wrapper page, not the d.vinci tenant itself
# (romed-jobs.de and jobs.klinikum-fuerth.de happen to *be* the tenant host, but www.ukw.de/jobs,
# klinikum-neumarkt.de and sozialstiftung-bamberg.de are not -- they embed the real tenant via a
# jobWidgetLoader script or a plain outbound link). Resolve that host first.
DVINCI_TENANT = re.compile(r"https?://([a-z0-9][a-z0-9.\-]*?\.dvinci-(?:easy|hr)\.com)", re.I)
DVINCI_LINK = re.compile(r'https?://([a-z0-9][a-z0-9.\-]*)/de/(?:jobs|p/[^/"\']+/jobs)/', re.I)


def dvinci_host(careers_url, session=None):
    """Return the host that answers /jobPublication/list.json for this board, or None."""
    own = urlparse(careers_url or "").netloc
    if own:
        r = get("https://%s/jobPublication/list.json" % own, session=session)
        if r and r.ok:
            try:
                if isinstance(r.json(), list):
                    return own
            except Exception:
                pass
    r = get(careers_url, session=session) if careers_url else None
    if not r or not r.ok:
        return None
    m = DVINCI_TENANT.search(r.text)
    if m:
        return m.group(1)
    for h in DVINCI_LINK.findall(r.text):
        if h != own:
            return h
    return None


def parse_dvinci(j, org, list_url):
    """One entry of jobPublication/list.json -> our row shape.

    jobOpening.location is free text -- usually a town ("Rosenheim") but sometimes the facility
    name itself ("Klinikum Bamberg", seen when the tenant has no structured address at all). Reject
    the latter shape here rather than mislabel it as a city; the caller fills in the registry town.
    """
    jo = j.get("jobOpening") or {}
    addr = ((jo.get("locations") or [{}])[0] or {}).get("address") or {}
    loc_text = jo.get("location")
    if loc_text and re.search(r"klinik|krankenhaus|hospital|zentrum|stiftung|gmbh", loc_text, re.I):
        loc_text = None
    city = addr.get("city") or loc_text
    desc = _txt(" ".join(_html.unescape(p) for p in
                         (j.get("introduction"), j.get("tasks"), j.get("profile"), j.get("weOffer")) if p))
    url = j.get("jobPublicationURL") or list_url
    employment_type = ", ".join(wt.get("name") for wt in (jo.get("workingTimes") or []) if wt.get("name")) or None
    return {"title": j.get("position"), "org": (jo.get("company") or {}).get("name") or org,
            "loc": [{"city": city, "plz": addr.get("zipCode"), "region": None}],
            "url": url, "page": url,
            # createdDate (posting date) not startDate (Eintrittsdatum/job start date) -- startDate is
            # null on most postings anyway, but the field itself is the wrong signal for "date posted".
            "datePosted": (jo.get("createdDate") or "")[:10] or None,
            "employmentType": employment_type,
            "department": jo.get("department"), "reference": jo.get("reference"),
            "description": desc}


def crawl_dvinci(c, session=None):
    cu = (c.get("careers_url") or "").strip()
    if not cu:
        return []
    host = dvinci_host(cu, session=session)
    if not host:
        return []
    list_url = "https://%s/jobPublication/list.json" % host
    r = get(list_url, session=session)
    jobs = []
    if r and r.ok:
        try:
            data = r.json()
            if isinstance(data, list):
                jobs = data
        except Exception:
            jobs = []
    if not jobs:
        # list.json 404s on some tenants; the same data is inlined on the listing page as a JS var.
        r2 = get("https://%s/de/jobs" % host, session=session)
        if r2 and r2.ok:
            m = re.search(r"var DvinciData\s*=\s*(\{.*?\});", r2.text, re.S)
            if m:
                try:
                    data = json.loads(m.group(1))
                    jobs = data.get("jobPublications") or data.get("jobs") or []
                except Exception:
                    jobs = []

    # Section-first (surveyed 2026-09, revised): jobOpening.categories[].name is a real taxonomy
    # already sitting in the same list.json response fetched above (jobOpening.department, used in
    # parse_dvinci, is a different -- more granular -- field). This used to hard-narrow `jobs` to
    # only the matched category -- but on two real boards (RoMed Rosenheim, Sozialstiftung Bamberg)
    # that dropped genuine certified-nursing postings filed under other categories entirely
    # ("Berufsfachschule für Pflege", teaching-school buckets; "B-PD-ANAE"/"OTK", functional-unit
    # codes) -- 5/36 and 5/91 real, measured losses, unambiguous titles like "Advanced Practice
    # Nurses", "Pflegerische Funktionsleitung". `jobs` here all came from the ONE list.json request
    # already made above (no per-job detail fetch at all), so narrowing saved zero network cost --
    # keep every job, and thread each job's own category name(s) into the payload for
    # classify.classify_role's nursing_section_confirmed signal downstream instead of using it as a
    # fetch filter.
    def _cat_names(job):
        return [(cat.get("name") or cat.get("internalName") or "")
                for cat in ((job.get("jobOpening") or {}).get("categories") or [])]

    # list.json is one response with every posting -- no pagination exists to walk and no cap to
    # respect; jobs[:max_jobs] used to silently truncate boards past 300 for no benefit.
    out = []
    for j in jobs:
        p = parse_dvinci(j, c["name"], list_url)
        if not p.get("title"):
            continue
        if not p["loc"][0]["city"] and c.get("town"):
            p["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
        p["section_labels"] = _cat_names(j)
        out.append(row(host, p["url"], p, "dvinci"))
    return out


# ---------------------------------------------------------------------------
# Group portals: many census sites share one employer-group job board
# ---------------------------------------------------------------------------
# Nine kbo clinics all point at kbo.de's TYPO3/Solr board, whose job URLs live on the *group*
# domain, not on each clinic's own host — so the per-site sitemap walk finds nothing. Listing pages
# are paginated with tx_solr[page]; detail pages carry a complete JSON-LD JobPosting.
GROUP_PORTALS = [
    # Not anchored to the string start: a clinic's own NAME rarely starts with "kbo-" (e.g. 16107
    # "Zentrum für psychische Gesundheit (ZPG) Ingolstadt", the kbo-Donau-Altmühl-Kliniken site --
    # missed entirely by the old `^kbo-` anchor, confirmed live 2026-09-11 that kbo-dak.de/karriere
    # itself links straight to kbo.de/karriere/jobboerse filtered to this sub-brand) -- match "kbo-"
    # or "kbo.de" ANYWHERE in name+careers_url so every kbo-<subbrand> site is caught generically,
    # not just the ones enumerated by name so far.
    {"match": r"kbo-|kbo\.de",
     "list": "https://kbo.de/karriere/jobboerse",
     # decoded on purpose: crawl_group_portal urlencodes it (pre-encoded value paged nothing, 10 vs 109 jobs)
     "page_param": "tx_solr[page]",
     "job_rx": r"https://kbo\.de/karriere/jobs/[^\"'\s>]+", "host": "kbo.de",
     # Every posting's own JSON-LD jobLocation is the kbo GROUP's Munich headquarters address
     # (Prinzregentenstraße 18) -- never the real work site of any of its 32 Bavaria clinics
     # (confirmed live 2026-09-11 across 3 sampled postings, different sub-brands, same address
     # every time). The real site is only named in the title's own trailing "in <Ort>" / "am
     # Standort <Ort>" / "des Standorts <Ort>" text, when present at all -- best-effort, not every
     # posting names one (e.g. a bare "Pflegefachhelfer (m/w/d)" carries no location clue anywhere).
     "title_city_rx": re.compile(r"(?:\bin\b|am Standort|des Standorts)\s+([A-ZÄÖÜ][\wäöüß.\-]*"
                                  r"(?:\s+(?:an|am|a\.\s?d\.|i\.\s?d\.)\s+[\wäöüß.\-]+)?"
                                  r"(?:\s+[A-ZÄÖÜ][\wäöüß.\-]*){0,2})$")},
    # Barmherzige Brüder run one board for all their Bavarian houses.
    {"match": r"barmherzige", "list": "https://karriere.barmherzige.net/jobs/",
     "page_param": "c_page",
     "job_rx": r"https://karriere\.barmherzige\.net/jobs/[a-z0-9][^\"'\s>?]+", "host": "karriere.barmherzige.net"},
]


def group_portal_for(c):
    name = (c.get("name") or "") + " " + (c.get("careers_url") or "")
    for g in GROUP_PORTALS:
        if re.search(g["match"], name, re.I):
            return g
    return None


def crawl_group_portal(c, g, session=None, max_jobs=100_000):  # loop-safety ceiling, not a board-size cap
    """Page the group board, then read each job's JSON-LD. Shared across every site of the group.

    Some clinics carry their own pre-filtered querystring on the shared board (e.g.
    ?filter[company][]=<this clinic>) in their registry careers_url -- start from that page rather
    than the group's bare listing URL, so a per-clinic filter that the tenant's own server already
    honours isn't silently dropped in favour of the whole group's unfiltered job list.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    cu = c.get("careers_url") or ""
    base_list = cu if g["host"] in cu else g["list"]
    parts = urlsplit(base_list)
    base_qs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != g["page_param"]]
    urls, seen = [], set()
    for i in range(1, g.get("pages", 100_000) + 1):  # 100_000: loop-safety ceiling, board's own empty page stops it
        qs = base_qs if i == 1 else base_qs + [(g["page_param"], str(i))]
        u = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(qs), ""))
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
            title_city_rx = g.get("title_city_rx")
            if title_city_rx:
                m = title_city_rx.search(j["title"].strip())
                if m:
                    j["loc"] = [{"city": m.group(1), "plz": j["loc"][0].get("plz"), "region": j["loc"][0].get("region")}]
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
    "oracle": crawl_oracle,
    "dvinci": crawl_dvinci,
    "wp_jobs": crawl_wp_jobs,   # routing.py default for careers_url-but-no-vendor-label boards
    "self_hosted": crawl_wp_jobs,  # discovery found no vendor fingerprint; try the generic reader anyway
}
# beesite/hr4you have no registry label (TASK-40 AC#3: crawl_wp_jobs above probes and delegates to
# them by capability) -- these two entries only matter if a future census run ever does fingerprint
# either vendor by name; imported here, not at module top, since both modules are self-contained
# (no import back into this one) and this keeps the plain vendor -> function table one lookup deep.
from pflege_jobs.sources.beesite import crawl_beesite as _crawl_beesite  # noqa: E402
from pflege_jobs.sources.hr4you import crawl_hr4you as _crawl_hr4you  # noqa: E402
VENDORS["beesite"] = _crawl_beesite
VENDORS["hr4you"] = _crawl_hr4you


def load_clinics(vendors):
    key = os.environ["SUPABASE_ANON_KEY"]
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
