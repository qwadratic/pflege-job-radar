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


def _section_keep(labels, nursing_label):
    """Section-first gate for a single job once a board-wide nursing category/label has been
    identified (nursing_label truthy). `labels` is that job's OWN category/department label(s) --
    a single string, a list of strings, or None/empty when the vendor didn't tag this particular
    job. Keep the job unless it carries an explicit label that is NOT the nursing one: an untagged
    job might still be a real nursing posting (some vendors only tag some jobs), so absence of a
    label must never be read as exclusion -- only a confidently different label is.
    """
    if not nursing_label:
        return True
    if isinstance(labels, str):
        labels = [labels] if labels else []
    labels = [l for l in (labels or []) if l]
    if not labels:
        return True
    return bool(section.pick_nursing_category(labels))


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
                # Some personio tenants (e.g. bergmanclinics) run one nationwide feed shared by many
                # unrelated facilities, with <subcompany> naming the actual site per job. When the
                # feed clearly spans more than one facility, narrow to jobs whose org text plausibly
                # names *this* clinic (token overlap, same heuristic pflege_jobs.registry uses to
                # link postings to clinics) -- fail open to the full list if nothing matches, so a
                # clinic whose own name just doesn't textually resemble its subcompany field never
                # loses all its jobs.
                orgs = {j.get("org") for j in jobs if j.get("org")}
                if len(orgs) > 1:
                    from pflege_jobs.registry import toks, overlap
                    # Prefer <office> (a bare per-site label, e.g. "Hofgartenklinik Aschaffenburg")
                    # over <subcompany>/org for the match text: subcompany usually repeats the
                    # tenant's own group brand ("Bergman Clinics ...") on every job regardless of
                    # site, which token-overlaps enough with a clinic's own "Bergman Clinics <site>"
                    # name to false-match a *different* site in the same group (verified live:
                    # "Bergman Clinics Augenklinik Weinheim GmbH" otherwise scores 0.5 against
                    # "Bergman Clinics Hofgartenklinik Aschaffenburg" on shared "bergman"/"clinics"
                    # tokens alone) -- office_raw carries no such shared brand noise.
                    want = toks(c.get("name"))
                    narrowed = [j for j in jobs
                               if overlap(want, toks(j.get("office_raw") or j.get("org"))) >= 0.5]
                    if narrowed:
                        jobs = narrowed
                # Section-first: <department>/<recruitingCategory> come for free in the one feed
                # request already made above. When the tenant's taxonomy names a nursing department,
                # narrow to it; an untagged posting is still kept (some tenants only tag some jobs).
                # Surveyed 2026-09: no real coverage loss found on either tested personio board (one
                # never narrows at all -- specialty-shaped taxonomy with no nursing label; the other's
                # narrowing dropped only non-nursing postings) -- fetch-side behaviour is unchanged.
                nursing_label = section.pick_nursing_category([j.get("department") for j in jobs])
                if nursing_label:
                    narrowed = [j for j in jobs if _section_keep(j.get("department"), nursing_label)]
                    if narrowed:
                        jobs = narrowed
                for j in jobs:
                    j["section_labels"] = [j["department"]] if j.get("department") else []
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
        dept_label = (p.get("department") or {}).get("label")
        out.append({"title": p.get("name"), "org": (p.get("company") or {}).get("name") or org,
                    "loc": [{"city": loc.get("city"), "plz": loc.get("postalCode"), "region": loc.get("region")}],
                    "url": p.get("ref") or p.get("applyUrl") or
                           "https://jobs.smartrecruiters.com/%s/%s" % ((p.get("company") or {}).get("identifier", ""), p.get("id", "")),
                    "page": page_url, "datePosted": (p.get("releasedDate") or "")[:10] or None,
                    "employmentType": ((p.get("typeOfEmployment") or {}).get("label")),
                    "department": dept_label,
                    "section_labels": [dept_label] if dept_label else [],
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
    limit = 100
    ceiling = 1000
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
    offset = limit
    while offset < ceiling:
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
    out = [row("jobs.smartrecruiters.com", j["url"], j, "smartrecruiters")
           for j in parse_smartrecruiters({"content": content}, c["name"], first_url) if j.get("title") and j.get("url")]
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


# The "Berufsfeld" filter fieldset (only rendered on boards with enough jobs/categories to need
# one): <label for="category_<hash>">Pflege (1 Treffer)</label>, whose <hash> works as
# ?category[]=<hash> on the very same joblist URL.
HELIX_CATEGORY = re.compile(r'for="category_([a-f0-9]+)"[^>]*>([^<]*)</label>', re.I)


def _helix_nursing_query(htmltext):
    """-> the category hash for the nursing Berufsfeld option, or None if the board has no such
    filter (small boards render no fieldset at all) or none of its options name nursing."""
    options = [(h, _txt(label)) for h, label in HELIX_CATEGORY.findall(htmltext or "")]
    nursing_label = section.pick_nursing_category([label for _, label in options])
    if not nursing_label:
        return None
    for h, label in options:
        if label == nursing_label:
            return h
    return None


def crawl_helix(c, session=None):
    cu = c.get("careers_url") or ""
    r = get(cu, session=session)
    if not r or not r.ok:
        return []
    jobs = parse_helix(r.text, r.url, c["name"], r.url)
    listing_url, listing_html = r.url, r.text
    if not jobs:                                     # career page may only link to the joblist
        for m in re.finditer(r'href="([^"]*(?:joblist|helixjobs[^"]*)[^"]*)"', r.text, re.I):
            r2 = get(urljoin(r.url, m.group(1)), session=session)
            if r2 and r2.ok:
                jobs = parse_helix(r2.text, r2.url, c["name"], r2.url)
                if jobs:
                    listing_url, listing_html = r2.url, r2.text
                    break
    if not jobs:
        return crawl_wp_jobs(c, session=session)     # not a helix tenant after all

    # Section-first: a Berufsfeld=Pflege filter narrows the very same joblist URL server-side.
    cat_hash = _helix_nursing_query(listing_html)
    if cat_hash:
        sep = "&" if "?" in listing_url else "?"
        narrowed_url = "%s%scategory%%5B%%5D=%s" % (listing_url, sep, cat_hash)
        rn = get(narrowed_url, session=session)
        if rn and rn.ok:
            narrowed_jobs = parse_helix(rn.text, rn.url, c["name"], rn.url)
            if narrowed_jobs:
                jobs = narrowed_jobs

    host = urlparse(cu).netloc
    return [row(host, j["url"], j, "helix") for j in jobs]


# ---------------------------------------------------------------------------
# WordPress / TYPO3 "jobs" sites + concludis tenants: sitemap -> detail pages
# ---------------------------------------------------------------------------
JOB_SITEMAP = re.compile(r"(jobs?|stellen|karriere|career|vacan)", re.I)
JOB_PATH = re.compile(r"/(jobs?|stellen?|stellenangebote?|stellenanzeigen?|karriere/stellen|vacan)[/-]", re.I)
# Loose fallback: a detail URL directly under a job-ish path segment (e.g. Wix's bare
# /karriere/<slug>), only tried against locs pulled from a sitemap whose own URL already
# matched JOB_SITEMAP (so it's not applied to a site's whole, unfiltered sitemap).
JOB_PATH_LOOSE = re.compile(r"/(jobs?|stellen?|stellenangebote?|stellenanzeigen?|karriere|vacan)/[^/]+$", re.I)


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
    found = [u for u in dict.fromkeys(out) if JOB_PATH.search(u)]
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
            cand = re.split(r"\s+[–—|]\s+", cand)[0].strip()
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
    return {"title": title, "org": org, "loc": [{"city": None, "plz": None, "region": None}],
            "url": url, "page": url, "description": _txt(body)}


def _wp_job_rows(urls, c, host, max_jobs, session, section_labels=None):
    out = []
    for u in urls[:max_jobs]:
        r = get(u, session=session)
        if not r or not r.ok:
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


def _page_job_links(url, session=None, exclude=()):
    """Job-looking links on one page, minus `exclude` -- the site-wide nav/footer (Berufsgruppe
    siblings, "Karriere"/"Impressum"/...) repeats on every subpage, so without excluding whatever
    already linked from the entry page, a "narrower" section page can resurface the whole board."""
    r = get(url, session=session)
    if not r or not r.ok:
        return []
    base = _page_base(r)
    html = re.sub(r"(?s)<!--.*?-->", "", r.text)
    found = (urljoin(base, h) for h in re.findall(r'href="([^"#]+)"', html) if JOB_PATH.search(h))
    return list(dict.fromkeys(u for u in found if u not in exclude))


def _listing_page_key(u):
    """Normalize a URL for "is this the listing page itself" comparisons -- strip index.php/.html
    and a trailing slash so http://x/stellenangebote/ == http://x/stellenangebote/index.php."""
    p = urlparse(u)
    path = re.sub(r"/index\.(php|html?)$", "/", p.path or "/", flags=re.I).rstrip("/") or "/"
    return (p.netloc.lower(), path.lower())


def crawl_wp_jobs(c, session=None, max_jobs=int(os.environ.get("VENDOR_MAX_JOBS", "300"))):
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
    section_label, section_url = _wp_nursing_section_url(cu, cu_resp)
    out, fetched = [], set()
    if section_url:
        cu_links = ({urljoin(_page_base(cu_resp), h) for h in re.findall(r'href="([^"#]+)"', cu_resp.text)}
                    if cu_resp and cu_resp.ok else set())
        section_urls = [u for u in _page_job_links(section_url, session=session, exclude=cu_links | {section_url})
                        if _listing_page_key(u) not in not_a_job]
        if section_urls:
            out = _wp_job_rows(section_urls, c, host, max_jobs, session,
                               section_labels=[section_label] if section_label else None)
            fetched = {j["payload"]["url"] for j in out}

    urls = [u for u in find_job_urls(base, session=session) if _listing_page_key(u) not in not_a_job]
    remaining = max(max_jobs - len(out), 0)
    if urls and remaining:
        more_urls = [u for u in urls if u not in fetched]
        out = out + _wp_job_rows(more_urls, c, host, remaining, session)
    if not out:
        # Either the sitemap had no job urls, or every one of them was stale/unfetchable (some TYPO3
        # sitemaps carry dead query-string job routes while the career page itself links the live,
        # friendly-slug job pages) -- fall back to scanning the career page's own job links.
        cu_html = re.sub(r"(?s)<!--.*?-->", "", cu_resp.text) if cu_resp and cu_resp.ok else ""
        page_urls = ([urljoin(_page_base(cu_resp), h) for h in re.findall(r'href="([^"#]+)"', cu_html) if JOB_PATH.search(h)]
                     if cu_resp and cu_resp.ok else [])
        page_urls = [u for u in dict.fromkeys(page_urls) if _listing_page_key(u) not in not_a_job]
        if page_urls and page_urls != urls:
            out = _wp_job_rows(page_urls, c, host, max_jobs, session)
    return out


def crawl_oracle(c, session=None):
    """Oracle Recruiting Cloud is a client-rendered SPA -- no server-rendered job links to walk a
    sitemap for. Some tenants (softgarden-fronted, e.g. karriere.josef.de) also publish a public,
    unauthenticated schema.org DataFeed at <origin>/jobs.feed.json; prefer that when present, since
    it has full descriptions and needs no browser. Falls back to crawl_wp_jobs for tenants that
    render job links server-side instead (Klinikum FFB)."""
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
    return crawl_wp_jobs(c, session=session)


REXX_JOB_HREF = re.compile(r'href="([^"#]*-j\d+\.html[^"]*)"')
REXX_TAG = re.compile(r'<span[^>]*class="[^"]*job_details_first[^"]*"[^>]*>(.*?)</span>', re.S)


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
    urls, tags, seen, base = [], {}, set(), None
    for start in range(0, 1000, 100):
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
        if fresh == 0 or len(urls) >= max_jobs:
            break
        time.sleep(0.2)
    if base is None:
        return []

    # Section-first (surveyed 2026-09, revised): the listing already tags every job with a
    # Fachbereich label (no extra request). This used to also HARD-FILTER which detail pages got
    # fetched at all -- but on a real board (Schön Klinik group, 296 postings) that dropped 30
    # genuine certified-nursing postings filed under a different Fachbereich than the one nursing
    # bucket ("OP Pfleger oder Operationstechnischer Assistent", "MFA/MTRA/Pflegefachkraft
    # Herzkatheterlabor..."), a real, measured coverage loss. `urls` here is already capped at
    # max_jobs during pagination above, so fetching every one of them costs no more requests than the
    # cap already allowed -- fetch them all, and thread each job's own Fachbereich tag (already sitting
    # in `tags`, no extra request) into the payload for classify.classify_role's
    # nursing_section_confirmed signal downstream instead of using it as a fetch filter.
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
        j["section_labels"] = [tags[u]] if tags.get(u) else []
        out.append(row(urlparse(base).netloc, j["url"], j, "rexx"))
        time.sleep(0.2)
    return out


# mein-check-in.de hosts one tenant per employer; the careers page just links into it.
MCI_TENANT = re.compile(r"mein-check-in\.de/([a-z0-9][a-z0-9_-]*)/?", re.I)


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

    # Section-first: the overview page already groups every position under a sidebar heading
    # (<li id="pg-<id>"><span>Pflegedienst</span><ul>...position-<id> links...</ul></li>) -- no
    # extra request needed to read which ids belong to the nursing group.
    pid_group = {}
    groups = re.findall(r'<li id="pg-\d+"[^>]*>\s*<span[^>]*>(.*?)</span>(.*?)(?=<li id="pg-|\Z)', listing.text, re.S)
    nursing_label = section.pick_nursing_category([_txt(label) for label, _ in groups]) if groups else None
    if nursing_label:
        for label, body in groups:
            lbl = _txt(label)
            for pid in re.findall(r'position-(\d+)', body):
                pid_group.setdefault(pid, lbl)

    seen, out = set(), []
    for pid, inner in re.findall(r'<a[^>]+position-(\d+)[^>]*>(.*?)</a>', listing.text, re.S):
        if pid in seen:
            continue
        if not _section_keep(pid_group.get(pid), nursing_label):
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
    return {"title": j.get("position"), "org": (jo.get("company") or {}).get("name") or org,
            "loc": [{"city": city, "plz": addr.get("zipCode"), "region": None}],
            "url": url, "page": url,
            "datePosted": (j.get("startDate") or "")[:10] or None,
            "department": jo.get("department"), "reference": jo.get("reference"),
            "description": desc}


def crawl_dvinci(c, session=None, max_jobs=int(os.environ.get("VENDOR_MAX_JOBS", "300"))):
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

    out = []
    for j in jobs[:max_jobs]:
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
    for i in range(1, g.get("pages", 8) + 1):
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
}


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
