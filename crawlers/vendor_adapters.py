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
import itertools
import json
import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs import section  # noqa: E402

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"}
OUT = os.environ.get("EGRESS_OUTPUT_DIR", "crawl_vendors")
CID = "vendor-adapters-" + os.environ.get("EGRESS_CLIENT", "default")
PROJECT = os.environ.get("SUPABASE_PROJECT_URL", "https://klkxfvieaxpjlplloljn.supabase.co")

# The parenthesized (m/w/d) form plus the gender-neutral colon/asterisk suffix ("Pfleger:in",
# "Mitarbeiter*in") some boards use instead -- missing the second form dropped every live posting
# on klinik-steger.de once it switched styles (confirmed live 2026-09-18: _faqpage_job_rows'
# gender-gate, added the same day to keep an application FAQ from being read as a posting, would
# otherwise have silently dropped this board's own real nursing postings along with it).
# Plus the bare-slash suffix form ("Pfleger/in", "Pfleger/innen", "Angestellte/r") with no colon or
# asterisk -- a separate, equally standard German convention, missing from every existing branch here
# (confirmed live 2026-09-23: barmherzige-bieten-zukunft.de/stellenmarkt/stellenboerse titles this
# way; the pre-existing GENDER gate treated ~30 of its own real postings as index-page noise and
# recursed into them instead of storing a row). `[/]\s?in\b` alone would also match a stray "/in" in
# unrelated prose; requiring the slash directly precede "in"/"innen"/"r" with no space in between
# keeps this to the actual grammatical suffix-pairing notation, not a loose word-boundary guess.
GENDER = re.compile(r"\((?:m|w|d|x|i|gn)\s?[/|*]\s?(?:m|w|d|x|i|gn)(?:\s?[/|*]\s?(?:m|w|d|x|i|gn))?\)|[:*]in\b|/(?:innen|in|r)\b", re.I)


# An immediate ("0;url=...") client-side redirect stub (confirmed live 2026-09-22:
# psychiatrie-werneck.de's careers_url is nothing but this, to bezirk-unterfranken.helixjobs.com) --
# `requests`' own allow_redirects only ever follows an HTTP 3xx Location header, never this HTML-level
# equivalent, so every caller of get() saw only the tiny stub page and never the real board. Gated on
# delay=="0" specifically (not "refresh after N seconds"): a longer delay is usually a session-timeout
# or please-wait notice meant for a human to read, not "this page IS a redirect", and auto-following
# THAT would silently skip real content a slower page still has.
META_REFRESH_RX = re.compile(r'<meta[^>]+http-equiv=["\']refresh["\'][^>]*content=["\']0\s*;\s*url=([^"\']+)', re.I)


def get(u, timeout=30, session=None):
    # Every call for one board fetch shares one requests.Session (app/crawl.py's _fetch_board) --
    # tallying attempts/oks on it lets that caller tell a real transport failure (every request to
    # this board failed) from a board that was genuinely read and simply has nothing on it right
    # now (TASK-72 AC#1): a swallowed exception and a 404 while probing an alternate candidate URL
    # looked identical to every caller before this, so a whole board down for the night reported
    # the same "0 rows, no error" as an empty one.
    if session is not None:
        session._attempts = getattr(session, "_attempts", 0) + 1
    seen, r = set(), None
    try:
        r = (session or requests).get(u, headers=H, timeout=timeout, allow_redirects=True)
        # bounded the same way a browser's own redirect-chain limit is -- two stub pages pointing at
        # each other must not spin forever, but 5 hops is far more than any real single meta-refresh
        # stub (always one hop in practice) ever needs.
        for _ in range(5):
            m = r.ok and META_REFRESH_RX.search(r.text[:4000])
            if not m:
                break
            nxt = urljoin(r.url, _html.unescape(m.group(1).strip().strip('"\'')))
            if nxt == r.url or nxt in seen:
                break
            seen.add(nxt)
            r = (session or requests).get(nxt, headers=H, timeout=timeout, allow_redirects=True)
    except Exception:
        return None
    if session is not None and r.ok:
        session._ok = getattr(session, "_ok", 0) + 1
    return r


def _txt(s, limit=20000):
    # JSON-LD values are not always strings: the spec allows a bare number ("postalCode":81545 on
    # muenchen-klinik.de), an array of values, and a typed literal {"@value": ...}. Each of those
    # used to raise TypeError inside re.sub and abort the whole board walk on its first posting page.
    if isinstance(s, (list, tuple)):
        s = ", ".join(p for p in (_txt(v, limit) for v in s) if p)
    elif isinstance(s, dict):
        s = s.get("@value")
        if isinstance(s, (list, tuple, dict)):
            s = _txt(s, limit)
    if s is not None and not isinstance(s, str):
        s = str(s)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()[:limit] or None


def _sane_date(raw):
    """A `datePosted`/createdDate-shaped value from the source, sliced to its date part -- or None
    when absent or implausible. Several boards emit a JSON-LD epoch placeholder ("1970-01-01")
    instead of omitting the field; taking it verbatim marks the posting as decades-stale forever
    (app/data.py's freshness read trusts first_published as-is) rather than of unknown age -- 11
    open postings confirmed live 2026-09-18 (9 kbo.de, 2 frg-kliniken.de). A year before 2000 is
    never a real posting date on this board; anything else (including a malformed non-date string)
    is passed through unchanged, same as before."""
    d = (raw or "")[:10]
    return None if d[:4].isdigit() and d[:4] < "2000" else (d or None)


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
                    "datePosted": _sane_date(f("createdAt")),
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
                    "datePosted": _sane_date(p.get("date")), "department": None,
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
                    "page": page_url, "datePosted": _sane_date(p.get("releasedDate")),
                    "employmentType": ((p.get("typeOfEmployment") or {}).get("label")),
                    "department": dept_label,
                    "section_labels": [dept_label] if dept_label else [],
                    "description": p.get("description")})
    return out


def _smartrecruiters_ident(html):
    """The tenant identifier the widget itself uses -- present directly as company_code on the
    page's own data-widget JSON (e.g. Klinik Vincentinum's /karriere/stellenangebote, which IS the
    listing page). Falls back to a jobs.smartrecruiters.com/<tenant>/<id> link, for boards that embed
    the widget on a category subpage instead (Artemed's own /karriere hub).

    Entity-decode first: a TYPO3-style page carries that same widget JSON inside an HTML *attribute*
    (data-widget="widget({&quot;company_code&quot;: &quot;ArtemedSE&quot;, ...})"), so the literal
    double quotes the config uses only exist after unescaping -- confirmed live 2026-09-21 on all
    three Artemed career pages (klinik-feldafing.de, artemed-muenchen-sued.de, artemedmuenchen.de),
    every one of which returned None here and fell through to a yield-0 crawl_wp_jobs walk."""
    text = _html.unescape(html or "")
    m = re.search(r'"company_code"\s*:\s*"([A-Za-z0-9\-_]+)"', text)
    if m:
        return m.group(1)
    m = re.search(r"jobs\.smartrecruiters\.com/(?:ni/)?([A-Za-z0-9\-_]+)/[0-9a-f-]{8,}", text)
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
            ho_name = (o.get("name") if isinstance(o, dict) else o) or None
            return {"title": _txt(n.get("title"), 300),
                    "org": ho_name or org, "org_source": None if ho_name else "seed",
                    "loc": [{"city": a.get("addressLocality"), "plz": a.get("postalCode"),
                             "region": a.get("addressRegion")}],
                    "employmentType": n.get("employmentType"),
                    "datePosted": _sane_date(n.get("datePosted")),
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
            # the detail page's own JSON-LD may name the real hiringOrganization -- the joblist
            # card (parse_helix) never does, it always carried the caller's seed clinic name.
            j["org"] = detail["org"]; j["org_source"] = detail.get("org_source")
        else:
            j["org_source"] = "seed"  # joblist card has no employer info of its own to fall back from
        if not j["loc"][0]["city"] and c.get("town"):
            j["loc"] = [{"city": c["town"], "plz": None, "region": None}]; j["city_source"] = "seed"
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
# `[/-]` (not bare `/`) before the keyword: a compound URL slug joins words with a hyphen, not a
# slash -- /beruf-karriere/aktuelle-stellenangebote/details/<slug> (confirmed live 2026-09-11:
# frg-kliniken.de) has "stellenangebote" preceded by "-", never matched the old bare-`/` anchor.
JOB_PATH = re.compile(r"[/-](jobs?|stellen?\w*|karriere/stellen|vacan)[/-]|/(karriere-)?detail/[^/?#]"
                       r"|tx_\w*jobliste%5[Bb]job%5[Dd]=\d+", re.I)
# A job-alert subscribe widget's own path ("/job-newsletter", concludis' "/jobletter") matches
# JOB_PATH on "job[/-]" alone -- it is a write-only email-signup page, never a posting (confirmed
# live: karriere.ameos.eu's own "/job-newsletter" parsed as a fake "Job-Newsletter" row).
# A "?kategorie=.../?category=..." query is a department FILTER on the board's own overview page,
# never a specific posting, even when that overview page's URL otherwise matches JOB_PATH by
# living under the same "/stellenanzeigen/"-style parent folder as real detail pages (confirmed
# live 2026-09-18: kwm-klinikum.de's career page links 5 such filtered overview variants, none of
# them a posting -- the real detail pages sit one hop deeper, inside each overview page's own
# links, at a JS-rendered .../details/?job=<uuid> route this static crawler cannot read either way;
# excluding the filtered overview from the candidate list at least stops it being mis-parsed as a
# posting under its own generic page title).
# TYPO3's generic "single record" detail view (tx_news and kin) renders at the exact same bare
# /detail/<id> shape JOB_PATH's un-prefixed alternative accepts for a job -- a news/press/blog/event
# section under that shape is not a posting no matter how it classifies afterwards (confirmed live
# 2026-09-18: klinikum-memmingen.de's /aktuelles/detail/ news archive, 1545 rows/run, 31 surviving
# the role filter as fake nursing postings). A medical-glossary entry is the same TYPO3 shape one
# folder over (confirmed live 2026-09-21: klinikum-msp.de's own /patienten-besucher/glossar/detail/
# fusspflege, a foot-care glossary definition, stored as an open nursing posting) -- "glossar" joins
# the same blacklist rather than JOB_PATH's own /detail/ alternative gaining a positive job-word
# requirement: tests/test_vendor_adapters.py's test_not_job_path_excludes_typo3_news_press_blog_event_
# detail_pages pins JOB_PATH matching the BARE /aktuelles|presse|blog|.../detail/ shape on purpose, so
# NOT_JOB_PATH has something to positively exclude; narrowing JOB_PATH itself would have to un-match
# that same fixture.
# A slugified gender marker (confirmed live 2026-09-22: clinicum-stgeorg.de's own detail links are
# plain post slugs, e.g. ".../gesundheits-und-krankenpfleger-m-w-d-vollzeit-110488", no job/stellen/
# karriere keyword anywhere in the path -- and unlike klinik-menterschwaige.de's shape, the anchor's
# own visible TEXT carries no gender marker either, just a generic "Zum Jobangebot: <title>"). "(m/w/d)"
# and "(w/m/d)" are the only orders seen live across every board surveyed so far -- not widened to
# every theoretical permutation without evidence one exists.
SLUG_GENDER_RX = re.compile(r"[/-](?:m-w-d|w-m-d)(?:[/-]|$)", re.I)
NOT_JOB_PATH = re.compile(r"/job-?(?:news)?letter\b|[?&](kategorie|category)=|"
                          # A section word does not have to sit immediately before /detail/ -- a news
                          # article can nest its own /detail/ view one folder deeper still (confirmed
                          # live 2026-09-22: anregiomed.de's own postings 10453/10454/10455/10746,
                          # clinic 56101, all four real news press releases stored as open nursing
                          # postings under /aktuelles/neuigkeiten/detail/..., "neuigkeiten" the one
                          # immediately before /detail/, "aktuelles" one folder further out -- the old
                          # pattern only ever matched the word directly adjacent). (?:/[^/?#]+)* lets
                          # any number of path segments sit between the excluded word and /detail/;
                          # still anchored on one of the same named words, so a real "/karriere/.../
                          # detail/" job path (test_not_job_path_still_allows_real_karriere_detail_pages)
                          # is untouched -- none of those start with aktuelles/presse/news/blog/glossar/
                          # veranstaltungen/termine/events.
                          r"/(aktuelles?|presse|news|blog|glossar|veranstaltung(?:en)?|termine?|events?)(?:/[^/?#]+)*/(?:karriere-)?detail/", re.I)


def _registrable_domain(netloc):
    """Naive eTLD+1 (last two dot-separated labels, port stripped) -- this crawler only ever visits
    .de/.com/.org hospital domains, so a public-suffix-list dependency buys nothing a plain suffix
    compare doesn't already give _same_board below."""
    host = (netloc or "").split(":")[0].lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _same_board(url, base):
    """Is `url` on the same board as `base` (registrable domain)? A job-looking link embedded
    directly in one board's own HTML that points at an entirely different site's domain is not this
    board's posting -- confirmed live 2026-09-18: psychiatrie-werneck.de's own career page linking 8
    koenig-ludwig-haus.de rows, stamped with psychiatrie-werneck.de's own clinic name regardless.
    `base` is whatever host we most recently fetched (post-redirect), so a redirect the board's own
    server issues still passes -- only a link to an unrelated third host is rejected."""
    return _registrable_domain(urlparse(url).netloc) == _registrable_domain(urlparse(base).netloc)
# Loose fallback: a detail URL directly under a job-ish path segment (e.g. Wix's bare
# /karriere/<slug>), only tried against locs pulled from a sitemap whose own URL already
# matched JOB_SITEMAP (so it's not applied to a site's whole, unfiltered sitemap).
JOB_PATH_LOOSE = re.compile(r"/(jobs?|stellen?|stellenangebote?|stellenanzeigen?|karriere|vacan)/[^/]+$", re.I)
# TYPO3 "klinikumbasics/klinikumjobs" widget (München Klinik and sibling city-clinic sites): every
# division/category page under /jobs/<division>/ renders an empty <ul class="job-results"> -- the
# real postings sit only in this inline JSON var, client-filtered by JS. Its own <h1> is the
# division's marketing headline ("HEILEN KÖNNEN."), never a job title -- see _wp_job_rows.
ALLJOBS_RX = re.compile(r"var\s+allJobs\s*=\s*(\[.*?\])\s*;", re.S)


def find_job_urls(base, session=None, max_maps=500):  # loop-safety ceiling, not a real sitemap-index-size cap
    """Follow robots.txt + sitemap indexes, prefer a jobs-specific sitemap, return job detail URLs."""
    maps, out, job_out = [], [], []
    r = get(urljoin(base, "/robots.txt"), timeout=15, session=session)
    if r and r.ok:
        maps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)
    # "-index" (hyphen), not just "_index" -- confirmed live 2026-09-21: karriere.ge-passau.de serves
    # /sitemap-index.xml (200, -> /sitemap-0.xml, 26 /stellen/ job urls) while /sitemap_index.xml,
    # /sitemap.xml and /robots.txt all 404 -- every job on the board was invisible to this list before.
    maps += [urljoin(base, p) for p in ("/sitemap.xml", "/wp-sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml")]
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
            # Every unnamed child, not just the first 3 -- a wp-sitemap.xml whose children carry no
            # job-ish name at all (confirmed live 2026-09-18: klinikum-ab-alz.de) used to drop
            # children 4-8 permanently; max_maps above is what bounds the total walk now.
            queue = job_maps + queue if job_maps else queue + locs
        else:
            out += locs
            if JOB_SITEMAP.search(u):
                job_out += locs
    found = [u for u in dict.fromkeys(out) if JOB_PATH.search(u) and not NOT_JOB_PATH.search(u)]
    if not found and job_out:
        found = [u for u in dict.fromkeys(job_out) if JOB_PATH_LOOSE.search(u)]
    if not found:
        # No sitemap named a job at all -- before giving up, ask the board's own WP REST API for a
        # custom post type it kept OUT of every sitemap (confirmed live 2026-09-21: karriere.klinikum-
        # altmuehlfranken.de excludes its 'stellenangebote' CPT, 53 open postings, from robots.txt and
        # every /sitemap*.xml candidate above, but still answers /wp-json/wp/v2/stellenangebote).
        found = _wp_json_cpt_job_urls(base, session=session)
    if not found:
        # Silent zero-yield here is indistinguishable from "board has no jobs right now" --
        # but it is usually a JS-only site (sitemap has no job links) or an unmatched URL shape.
        # Surface it in the run log so these boards are visible instead of vanishing quietly.
        print("[wp_jobs] find_job_urls: no job links in sitemap for %s (%d sitemap urls seen)"
              % (base, len(out)), file=sys.stderr)
    return found


def _wp_json_cpt_job_urls(base, session=None):
    """A WordPress board's own REST API (/wp-json/wp/v2/types) names every registered post type --
    used only as a last resort, after every sitemap candidate above found nothing job-shaped, to catch
    a custom post type the board's own sitemap generator excludes (confirmed live 2026-09-21: see
    find_job_urls' caller). Each matched type's own rest_base then answers /wp-json/wp/v2/<rest_base>
    with every item's real, canonical detail-page `link` -- no separate parser needed, these urls feed
    straight into the same _wp_job_rows fetch-and-parse path a sitemap url would."""
    r = get(urljoin(base, "/wp-json/wp/v2/types"), timeout=20, session=session)
    if not r or not r.ok:
        return []
    try:
        types = r.json()
    except ValueError:
        return []
    if not isinstance(types, dict):
        return []
    cpts = [slug for slug, info in types.items()
            if slug not in ("post", "page", "attachment")
            and (JOB_SITEMAP.search(slug) or JOB_SITEMAP.search((info or {}).get("name") or ""))]
    if not cpts:
        return []
    # More than one job-vocabulary CPT can coexist (confirmed live: the same tenant also registers
    # 'stellenangebote_old') -- the shortest matching slug is the live one, a suffixed variant
    # ('_old', '_archiv', ...) is always the longer name and never the other way round.
    slug = min(cpts, key=len)
    rest_base = types[slug].get("rest_base") or slug
    out, page = [], 1
    while True:
        rr = get(urljoin(base, "/wp-json/wp/v2/%s?per_page=100&page=%d" % (rest_base, page)), timeout=25, session=session)
        if not rr or not rr.ok:
            break
        try:
            items = rr.json()
        except ValueError:
            break
        if not isinstance(items, list) or not items:
            break
        out += [it.get("link") for it in items if it.get("link")]
        # WP's own X-WP-TotalPages header is the site's own end-of-pagination signal. When it is
        # absent, that is not a stop signal -- the loop already has two of its own (not items /
        # not rr.ok above); stopping here too would silently ceiling every board at per_page=100
        # the moment a host omits this one header (review finding, 2026-09-22: this was a
        # self-invented ceiling inside the very fix for "reads short and reports success").
        total_pages = rr.headers.get("x-wp-totalpages")
        if total_pages and page >= int(total_pages):
            break
        page += 1
    return out


# A heading styled with Bootstrap's h1/h2/h3 utility class on a non-heading tag is still the
# posting's headline: karriere.klinikverbund-allgaeu.de renders every detail page's real title as
# <strong class="h1 font-weight-bolder"> and has no <h1>/<h2>/<h3> anywhere, so before this the
# board's own generic page <title> ("Karriere Detail - Klinikverbund Allgäu") became the title of
# all 82 of its postings -- one indistinguishable non-title for the whole board.
BOOTSTRAP_HEADING_RX = re.compile(r'<(strong|b|span|div|p)[^>]+class="[^"]*\bh[1-3]\b[^"]*"[^>]*>(.*?)</\1>',
                                  re.S | re.I)


def _headinglike(htmltext):
    """Sub-headings of a page, real tags first, then class-styled stand-ins."""
    return ([m.group(1) for m in re.finditer(r"<h[23][^>]*>(.*?)</h[23]>", htmltext or "", re.S)]
            + [m.group(2) for m in BOOTSTRAP_HEADING_RX.finditer(htmltext or "")])


# TASK-52: a generic application-flow label, not the job itself -- "Stellenanzeige" joins the
# original 3 (koenig-ludwig-haus.de's own <a> text is "Stellenanzeige <em>real title</em>", the same
# generic-label-first shape as the HubSpot "Stellenanzeige | <real title>" board this pattern was
# built for). Shared by parse_job_page's own <h1> cleanup and _wp_job_rows' anchor-text title
# fallback below, so both strip the same known labels instead of drifting apart.
GENERIC_TITLE_PREFIX_RX = re.compile(r"^(Bewirb dich als|Jetzt bewerben als|Stellenangebot:?|Stellenanzeige:?)\s+", re.I)
# A teaser-sentence anchor (koenig-ludwig-haus.de: "Der Bezirk ... sucht ... Pflegekraefte ...
# Lesezeit: 4 min.") carries the reading-time UI label as its own trailing sentence -- strip it same
# as any other chrome, not just the leading label above.
GENERIC_TITLE_SUFFIX_RX = re.compile(r"\s*Lesezeit:?\s*\d+\s*min\.?\s*$", re.I)
# Standard German legal-notice page titles -- never a job posting on any board, so the ungendered
# len(sub)<=1 fallback below (kept for the rare real posting with no gender marker) must not accept
# them just because they happen to be linked from a careers page with no other candidates alongside.
NOT_JOB_TITLE_RX = re.compile(r"^(Impressum|Datenschutzerkl.rung|Barrierefreiheitserkl.rung|"
                              r"Kontakt|AGB|Sitemap|Cookie-Einstellungen)\s*$", re.I)


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
                # ho_name is None when the page states no hiringOrganization at all -- distinguish
                # that from a real page-stated employer so the caller (and inbox.py's Matcher gate,
                # pflege_jobs/registry.py employer_inherited) can tell a page-read name from the
                # seed clinic's own name copied in as a guess.
                ho_name = (o.get("name") if isinstance(o, dict) else o) or None
                # The JSON-LD's own "url" is usually the canonical detail-page link -- but on a
                # board whose JSON-LD template names the SITE ROOT on every posting (confirmed live
                # 2026-09-18: komm-ins-klinikland.de), trusting it verbatim gives every posting the
                # SAME source_url and _post_inbox's dedupe-by-source_url collapses the whole board
                # to one row. Only accept it when it is on the fetched page's own host and its path
                # is not the bare root; otherwise the page we actually fetched is still the right,
                # distinct identity for this posting.
                n_url = n.get("url")
                if n_url:
                    same_host = urlparse(n_url).netloc.lower() == urlparse(url).netloc.lower()
                    is_root = (urlparse(n_url).path or "/") in ("", "/")
                    if not same_host or is_root:
                        n_url = None
                # Entity-decode the short JSON-LD strings, same as _txt already does for the
                # description: a board can HTML-escape inside the JSON string itself (confirmed
                # live 2026-09-21, jobs.bezirkskliniken-schwaben.de: addressLocality
                # "G&#252;nzburg"), and an escaped city never matches a registry town, so every
                # posting on such a board loses its location.
                return {"title": _txt(n.get("title"), 300), "org": _txt(ho_name, 300) or org,
                        "org_source": None if ho_name else "seed",
                        "loc": [{"city": _txt(a.get("addressLocality"), 200),
                                 "plz": _txt(a.get("postalCode"), 40),
                                 "region": _txt(a.get("addressRegion"), 200)}],
                        "url": n_url or url, "page": url,
                        # or _page_meta_date(...): a JobPosting block that states no datePosted of its
                        # own (or an epoch placeholder _sane_date already rejected) can still sit next
                        # to the same WP-SEO-meta/itemprop the no-JSON-LD branch below reads -- read
                        # off this SAME already-fetched htmltext rather than leave it for
                        # _enrich_wp_fallback_fields to re-fetch the page just to find it.
                        "datePosted": _sane_date(n.get("datePosted")) or _page_meta_date(htmltext),
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
        title = GENERIC_TITLE_PREFIX_RX.sub("", title)
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
        for raw in _headinglike(htmltext):
            cand = _txt(raw, 300)
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
    # No JobPosting JSON-LD reached this branch at all -- org is always the caller's seed-clinic
    # fallback here, never a page-stated name.
    return {"title": title, "org": org, "org_source": "seed",
            "loc": [{"city": facts.get("location"), "plz": None, "region": None}],
            "url": url, "page": url, "description": _txt(body),
            "employmentType": facts.get("schedule"),
            # TASK-85 AC#6: this is the branch a JSON-LD-less TYPO3 board (AMEOS: itemprop meta, no
            # JobPosting block at all, see ITEMPROP_DATE_RX) always took, so datePosted was always
            # empty here pre-fix -- _enrich_wp_fallback_fields then re-fetched every single one of
            # ~778 rows a second time just to run these same three regexes, serialized with its own
            # 0.5s sleep per row on top of _wp_job_rows' own 0.2s, ~doubling total requests and
            # stretching one run past 17 minutes before it had to be killed. Read it here instead, off
            # the SAME response already in hand, so the second fetch is never needed at all.
            "datePosted": _page_meta_date(htmltext)}


def _wp_job_rows(urls, c, host, session, section_labels=None, seen=None, titles=None, towns=None):
    """Fetch each url and turn it into a row -- except a klinikum-jobs widget page (ALLJOBS_RX),
    whose own title is never a job (see the regex's docstring): walk its embedded postings' own
    `link`s instead of accepting the division page itself as one fake row. `titles` (url -> anchor
    text, from the discovery step) backs a PDF-linked posting: its response body has no HTML to
    read a title from at all, so parse_job_page would otherwise just drop it."""
    seen = seen if seen is not None else set()
    titles = titles or {}
    out = []
    for u in urls:
        key = _fetch_dedupe_key(u)
        if key in seen:
            continue
        qkey = _query_id_key(u)
        if qkey and qkey in seen:
            continue
        seen.add(key)
        if qkey:
            seen.add(qkey)
        if PDF_LINK_RX.search(u):
            title = titles.get(u)
            if not title:
                continue
            j = {"title": title, "org": c["name"], "loc": [{"city": c.get("town"), "plz": None, "region": None}],
                 "city_source": "seed", "org_source": "seed",
                 "url": u, "page": u, "description": None, "employmentType": None}
            j["section_labels"] = list(section_labels) if section_labels else []
            out.append(row(host, u, j, "wp_jobs"))
            continue
        r = get(u, session=session)
        if not r or not r.ok:
            continue
        final_key = _fetch_dedupe_key(r.url)
        if final_key in seen and final_key != key:
            # A stale/expired job slug that 200s instead of 404ing (seen live: medbo.de) redirects
            # to one shared generic landing page instead -- a second, different, candidate slug
            # landing on a final url another candidate already claimed is that catch-all, not a
            # second distinct posting.
            continue
        seen.add(final_key)
        # The two shapes that answer 200 but are not the posting, decided by the same helpers the
        # verifier uses so a row cannot enter here and be expired hours later by the verify pass:
        # a bot wall's own refusal page, and a slug that redirected to the board's list.
        from pflege_jobs.verify import WALL_MARKERS, _bounced_to_list
        if WALL_MARKERS.search(r.text[:4000]) or _bounced_to_list(u, r.url, None):
            continue
        m = ALLJOBS_RX.search(r.text)
        if m:
            try:
                entries = json.loads(m.group(1))
            except Exception:
                entries = []
            widget_urls = [urljoin(r.url, e["link"]) for e in entries if e.get("link")]
            out += _wp_job_rows(widget_urls, c, host, session, section_labels, seen, titles, towns)
            continue
        j = parse_job_page(r.text, r.url, c["name"])
        if not j or not j.get("title"):
            continue
        if not GENDER.search(j["title"]):
            # A detail page whose own markup has no heading parse_job_page/_headinglike can read a
            # title from at all (confirmed live 2026-09-22: koenig-ludwig-haus.de -- the real title
            # sits only in a plain <span> with no h1-2-3/Bootstrap class, and every detail page on the
            # board repeats the SAME generic <title> tag) still linked here with real anchor text; if
            # THAT text is gender-marked, trust it instead of falling straight to the listing-page
            # guess below -- the same anchor-text fallback PDF_LINK_RX's branch above already relies on.
            anchor_title = titles.get(u)
            if anchor_title and GENDER.search(anchor_title):
                cleaned = GENERIC_TITLE_SUFFIX_RX.sub("", GENERIC_TITLE_PREFIX_RX.sub("", _txt(anchor_title, 300) or ""))
                j["title"] = cleaned.strip() or j["title"]
        if not GENDER.search(j["title"]):
            # parse_job_page accepts an ungendered <h1>/<title> too (its own weak fallback, for the
            # rare real posting whose title genuinely carries no marker) -- but the SAME weak fallback
            # is how a department/category INDEX page's own <h1> ("Stellenangebote", "Pflegedienst",
            # ...) gets mistaken for one job (confirmed live 2026-09-22, clinic 36202/csj.de: 10 of 17
            # stored rows were index pages under /beruf-und-karriere/stellenangebote/<dept>, including
            # .../alle-stellenangebote itself stored as a posting titled 'Stellenangebote' -- and the
            # real nursing postings one hop behind them, /berufsfelder/alle-stellenangebote/pflegedienst/
            # ..., were never read at all). A genuine listing always links several further job-shaped
            # pages of its own; a genuine single posting with an unusual, ungendered title (rare) does
            # not -- so only recurse into this page's own links, instead of accepting it as a row,
            # when it actually looks like a listing by that measure.
            sub = _job_link_pairs(r.text, r.url)
            if len(sub) > 1:
                # {**titles, **sub}, not a bare titles= or sub= -- the recursive candidates are the
                # SAME detID urls the outer discovery already gendered-anchor-checked (koenig-ludwig-
                # haus.de: every candidate here is already in the outer `titles`), dropping it made
                # anchor_title lookups above miss on the recursive pass and lose 7 of 10 real postings
                # (confirmed live 2026-09-22).
                out += _wp_job_rows(list(sub), c, host, session, section_labels, seen, {**titles, **sub}, towns)
                continue
            if NOT_JOB_TITLE_RX.search(j["title"]):
                # The meta-refresh follow (added this session, in the shared get()) newly lets a
                # site-wide legal-notice link resolve all the way to its target page instead of
                # bouncing on redirect -- confirmed live 2026-09-22: psychiatrie-werneck.de's own
                # Impressum/Barrierefreiheitserklaerung links landed here via this exact ungendered,
                # len(sub)<=1 fallback and were stored as two fake postings. Not board-specific: any
                # board can link these same standard German legal-notice pages from its careers page.
                continue
        if not j["loc"][0]["city"]:
            # TASK-118: a shared board with no structured location field at all can still state the
            # real work site in plain body prose ("am Standort Weilheim") -- confirmed live
            # 2026-09-23, meinkrankenhaus2030.de. Try that BEFORE the single-clinic seed-town
            # fallback, so a board this function's own caller has widened to more than one clinic
            # (crawlers.vendor_adapters.account_pool_for) does not get every row stamped with
            # whichever clinic happened to trigger this fetch.
            standort = extract_standort_city(j.get("description"), towns) if towns else None
            if standort:
                j["loc"] = [{"city": standort, "plz": None, "region": None}]
            elif c.get("town"):
                j["loc"] = [{"city": c["town"], "plz": None, "region": None}]; j["city_source"] = "seed"
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
# the href, not just the href. TYPO3's own "eID=dumpFile" download handler (confirmed live
# 2026-09-22: www.panorama-fachklinik.de, 3 postings) serves the identical PDF-flyer-as-only-detail
# shape but through a query-string handler with no ".pdf" anywhere in the URL at all -- same handling,
# widened match.
PDF_LINK_RX = re.compile(r"\.pdf(?:[?#]|$)|[?&]eID=dumpFile\b", re.I)


def _job_link_pairs(html, base, exclude=()):
    """(url, anchor text) for every job-looking link on one already-fetched page. `.*?` (not
    `[^<]*`) for the text span: a job title often sits inside a nested <span itemprop="title"> (seen
    live: karriere.ameos.eu's own schema.org microdata markup) -- requiring tag-free anchor content
    silently dropped every one of its links pre-fix. _txt() strips whatever tags are inside.

    A link counts as job-looking on EITHER signal, not just JOB_PATH on the href: a small clinic's
    WordPress site often gives each posting its own post slug with no job/stellen/karriere keyword
    in the URL at all (confirmed live 2026-09-11: klinik-menterschwaige.de's real detail pages are
    plain "/mitarbeiter-pflege-m-w-d-in-der-analytischen-milieutherapie/"-style permalinks), but the
    anchor's own visible TEXT still carries the gender marker every German job title does -- the
    same OR-of-href-or-text signal pflege_jobs/sources/career_crawl.py's _crawl_urls already uses,
    just missing here.

    Off-board links are dropped (see _same_board) UNLESS the same off-board host is linked repeatedly
    for distinct postings -- a host named only once is a stray cross-reference (confirmed live
    2026-09-18: psychiatrie-werneck.de naming one koenig-ludwig-haus.de posting, an unrelated
    hospital's own board), but a host linked repeatedly is not a stray mention, it is where this
    board's own vacancies actually live (confirmed live 2026-09-21: waldkrankenhaus.de's own careers
    page links jobs.malteser.de 31 times across distinct postings, its own host only twice -- TASK-85
    AC#3). The registry/board itself is what draws that line, never a link reached one hop further in
    (see _widget_endpoint_job_links, which stays same-board-only for exactly that reason)."""
    out, off_board = {}, {}
    for h, t in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S):
        text = _txt(t)
        if not ((JOB_PATH.search(h) or (text and GENDER.search(text)) or SLUG_GENDER_RX.search(h))
                and not NOT_JOB_PATH.search(h)):
            continue
        u = urljoin(base, _html.unescape(h))
        if u in exclude or u in out:
            continue
        if _same_board(u, base):
            out[u] = text
        else:
            off_board.setdefault(urlparse(u).netloc, {})[u] = text
    for links in off_board.values():
        if len(links) > 1:
            out.update(links)
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


# Bootstrap-style numbered pager (<ul class="pagination"><li class="page-item"><a class="page-link"
# href="...?...page=2...">2</a>): every page number is its own plain link, never labelled
# "next"/"weiter"/"»" (NEXT_PAGE_RX never fires), confirmed live 2026-09-23: Klinikum Kaufbeuren's
# TYPO3 board declares 16 Pflege rows under ?selection3=3, but only page 1's 10 render -- page 2's 6
# were silently never walked. Matches on the param name alone (page=/p=/seite=), not the "page-link"
# CSS class (a board's markup can rename it) -- scoped to same-path hrefs in _numbered_page_urls below
# so it can't wander onto an unrelated numbered link elsewhere on the page (a related-articles widget,
# a footer sitemap, ...).
# (?:^|[?&]), not a bare [?&] -- matched against urlparse(u).query, which has already had its
# leading "?" stripped, so a URL with page= as its ONLY param (no other param ahead of it to supply
# the "&") would otherwise never match at all (confirmed by its own test catching this: a bare
# "?page=2" url silently failed to be recognised as a numbered page while "?selection3=3&page=2"
# worked only by the accident of a second param providing the "&").
PAGE_PARAM_RX = re.compile(r"(?:^|[?&])(?:page|p|seite)=(\d+)", re.I)


def _numbered_page_urls(html, base, start_path):
    """Every distinct same-path listing URL whose query string carries a page-number param -- a
    board's own numbered pager, not a single "next" link. Not capped (TASK-14): a board can page as
    deep as it declares, and it is the frontier/visited bookkeeping in _paginated_job_links, not a
    guessed ceiling here, that stops the walk once every page number the board itself links has been
    seen."""
    out = []
    for h in re.findall(r'href="([^"]+)"', html):
        u = urljoin(base, _html.unescape(h))
        if urlparse(u).path == start_path and PAGE_PARAM_RX.search(urlparse(u).query):
            out.append(u)
    return out


# TYPO3 Extbase "pageable-container" list widget (oyc_template and similar): the page's own declared
# item-count (class="item-count") can exceed however many rows actually render server-side -- the
# rest sit behind a hidden JS/POST "load more" button with no plain-link pagination at all (neither
# NEXT_PAGE_RX nor _numbered_page_urls has anything to find here). Confirmed live 2026-09-23:
# karriere-barmherzige-muenchen.de/stellenangebote declares item-count=11 but only 10 <li
# class="list-item"> render; the controller honours a plain GET override of its own declared limit
# field (data-limit-field-name="tx_oycimport_list[limit]") -- requesting that many rows via GET
# renders all 11. Driven by the board's OWN declared total, not a guessed number: asking for exactly
# that many is never a cap, only wide enough to see everything the board itself says exists.
ITEM_COUNT_RX = re.compile(r'class="item-count"[^>]*>\s*(\d+)')
LIMIT_FIELD_RX = re.compile(r'data-limit-field-name="([^"]+)"')


def _extbase_widen_limit_url(url, html):
    """-> a GET url requesting at least as many rows as the page's own declared item-count, or None
    if this page isn't this widget shape (no item-count, or no limit-field name to widen)."""
    cm, lm = ITEM_COUNT_RX.search(html), LIMIT_FIELD_RX.search(html)
    if not cm or not lm:
        return None
    field = _html.unescape(lm.group(1))
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q[field] = cm.group(1)
    return p._replace(query=urlencode(q)).geturl()


# A career page can filter its listing to one site/category by default while its own facet <select>
# openly offers a wider "all" option -- confirmed live 2026-09-23:
# barmherzige-bieten-zukunft.de/stellenmarkt/stellenboerse (shared across the whole Barmherzige
# Brüder Regensburg group, 9 sites) defaults to 29 rows -- NOT "this one site only" as first assumed,
# some other implicit scope -- but its own tx_jrpersisjobs_fejrpersisjobs[location] select declares
# <option value="all">Alle Standorte</option>, and requesting that value renders 129 (a verified
# superset of the 29). Never guesses a param name off the page -- only widens a facet the page itself
# names, to the value the page itself offers, and only once (skips if that value is already set).
SELECT_ALL_RX = re.compile(r'<select[^>]+name="([^"]+)"[^>]*>((?:(?!</select>).)*?)</select>', re.I | re.S)
OPTION_ALL_VALUE_RX = re.compile(r'<option[^>]+value="(all|alle)"', re.I)


def _select_all_widen_url(url, html):
    """-> a GET url with the first facet <select> that offers a value="all"/"alle" option set to
    that value, or None if no such facet exists or it is already set that way."""
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    for name, body in SELECT_ALL_RX.findall(html):
        m = OPTION_ALL_VALUE_RX.search(body)
        if not m:
            continue
        field = _html.unescape(name)
        if q.get(field) == m.group(1):
            continue
        q2 = dict(q); q2[field] = m.group(1)
        return p._replace(query=urlencode(q2)).geturl()
    return None


def _paginated_job_links(start_url, session=None, exclude=(), first_resp=None, titles=None):
    """Job-looking links across a server-rendered listing's own pagination. Follows both shapes a
    board's pager comes in: a single "next page" link (NEXT_PAGE_RX/NEXT_PAGE_JSON_RX) AND a
    Bootstrap-style numbered pager where every page number is its own plain link and no page is ever
    labelled "next" (_numbered_page_urls) -- a board can expose either, or both, so the walk queues
    whatever it finds rather than assuming one shape. `first_resp` reuses an already-fetched page 1
    (crawl_wp_jobs already fetched `cu`) instead of re-fetching it. `titles`, if given, is filled
    in-place with each link's own anchor text (url -> text) -- the only real title a PDF-linked
    posting has, see PDF_LINK_RX.

    The walk's only stop condition is running out of frontier: every next-page link and every
    numbered-page link discovered so far has been visited. The `max_pages=200` ceiling this used to
    carry (TASK-14) was no caller's choice -- nothing ever passed it -- and it could only ever turn a
    board bigger than someone's guess into a short read reported as a complete one."""
    out, visited, start_path = [], set(), urlparse(start_url).path
    frontier, resp = [start_url], first_resp
    while frontier:
        url = frontier.pop(0)
        if not url or url in visited:
            continue
        visited.add(url)
        r = resp if resp is not None else get(url, session=session)
        resp = None
        if not r or not r.ok:
            continue
        base = _page_base(r)
        html = re.sub(r"(?s)<!--.*?-->", "", r.text)
        pairs = _job_link_pairs(html, base, exclude=exclude)
        if titles is not None:
            for u, t in pairs.items():
                titles.setdefault(u, t)
        out += [u for u in pairs if u not in out]
        nxt = _next_page_url(html, base)
        if nxt and nxt not in visited:
            frontier.append(nxt)
        for pu in _numbered_page_urls(html, base, start_path):
            if pu not in visited and pu not in frontier:
                frontier.append(pu)
        time.sleep(0.3)
    return out


# A job-filter/listing widget (medbo's TYPO3 cn_medbo_jobs extension) advertises its own AJAX read
# path as a `data-url` attribute on the career page itself -- the board's own client calls it (seen
# live: a job-shaped `?tx_..._joblist[action]=ajaxFilter...` url), so crawl_wp_jobs must too, even
# when every posting is already reachable through friendlier detail-page links found elsewhere.
DATA_URL_JOB_RX = re.compile(r'data-url="([^"]*(?:job|stellen)[^"]*)"', re.I)


def _widget_endpoint_job_links(cu_resp, session=None):
    """Off-board links are dropped (see _same_board) -- the widget's own AJAX response is on this
    board (fetched from a data-url the board's own page named, redirect included), but a link
    embedded in its returned content is not vouched for the same way."""
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
        out += [x for x in (urljoin(r.url, hh) for hh in re.findall(r'href="([^"#]+)"', content))
                if _same_board(x, r.url)]
    return list(dict.fromkeys(out))


# A career page can carry no listing HTML of its own at all, only a wholesale third-party <iframe>
# (confirmed live 2026-09-22: kh-as.de embeds jobs.maxime-media.de, a small vendor with no fingerprint
# elsewhere in this file -- plain server-rendered anchors, JSON-LD detail pages, nothing exotic, just
# never reachable because it lives on a different host entirely). Off-board is deliberate here, unlike
# _job_link_pairs' own off-board carve-out for a stray same-page link: an iframe's src IS the board
# the page chose to show, not a cross-reference one hop further in.
IFRAME_SRC_RX = re.compile(r'<iframe[^>]+src="([^"]+)"', re.I)


def _iframe_start_url(cu_resp):
    """-> the first off-board <iframe src> on an already-fetched career page, or None. Meant to be
    walked by _paginated_job_links exactly like the career page itself (same pagination-following,
    same job_link_pairs candidate rules) -- just against the iframe's own host."""
    if not cu_resp or not cu_resp.ok:
        return None
    m = IFRAME_SRC_RX.search(cu_resp.text)
    if not m:
        return None
    u = urljoin(cu_resp.url, _html.unescape(m.group(1)))
    return None if _same_board(u, cu_resp.url) else u


def _listing_page_key(u):
    """Normalize a URL for "is this the listing page itself" comparisons -- strip index.php/.html
    and a trailing slash so http://x/stellenangebote/ == http://x/stellenangebote/index.php.
    Deliberately query-blind: a pagination/filter variant of the SAME career/listing page must
    still be recognised as "not a job" (see crawl_wp_jobs' not_a_job set) even though its query
    differs from the bare URL first fetched. For "is this the same POSTING as another candidate"
    use _fetch_dedupe_key instead -- some boards distinguish postings only by a query parameter."""
    p = urlparse(u)
    path = re.sub(r"/index\.(php|html?)$", "/", p.path or "/", flags=re.I).rstrip("/") or "/"
    return (p.netloc.lower(), path.lower())


def _fetch_dedupe_key(u):
    """Like _listing_page_key, but keeps a normalised query string -- some boards distinguish
    postings ONLY by a query parameter (e.g. .../uebersicht-aller-stellen/details/?job=<uuid>), so
    collapsing every candidate onto (netloc, path) alone silently fetched just one of many
    (confirmed live 2026-09-18: Klinikum Wuerzburg Mitte, 53 live postings collapsed to 1; regressed
    2026-09-11, commit 7471cae). Used only for crawl_wp_jobs' own fetch/redirect-bounce dedup within
    _wp_job_rows, never for the not_a_job "is this the listing page itself" test, which intentionally
    stays query-blind."""
    p = urlparse(u)
    path = re.sub(r"/index\.(php|html?)$", "/", p.path or "/", flags=re.I).rstrip("/") or "/"
    q = urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))
    return (p.netloc.lower(), path.lower(), q)


def _query_id_key(u):
    """When a url carries exactly one query param, two DIFFERENT paths sharing that same param value
    are almost certainly board aliases for one posting, not two (confirmed live 2026-09-22:
    koenig-ludwig-haus.de serves both .../index.html?detID=1003 and
    .../20396.Stellenanzeigen.html?detID=1003 for the same posting) -- _fetch_dedupe_key keeps the
    path, so it treats them as distinct and doubles every row. Return a (netloc, param) key for this
    single-param case, None for ordinary multi-param pagination/filter urls where path still matters."""
    p = urlparse(u)
    qs = parse_qsl(p.query, keep_blank_values=True)
    return (p.netloc.lower(), qs[0]) if len(qs) == 1 else None


def _listing_dup(u, not_a_job, titles):
    """not_a_job's query-blind key check (see _listing_page_key) also swallows a genuine posting on
    boards that distinguish EVERY detail page from the listing ONLY by a query id on the identical
    path (confirmed live 2026-09-22: koenig-ludwig-haus.de's own postings are all
    .../jobs-im-klh/index.html?detID=NNN -- same normalized key as the listing itself, so all 13 were
    silently dropped before this). A bare pagination/filter link never carries a real job title as
    its own anchor text; trust that existing signal (already computed for `titles`, see
    _job_link_pairs) instead of guessing from the query shape which param names are "pagination"."""
    if _listing_page_key(u) not in not_a_job:
        return False
    if not urlparse(u).query:
        return True
    return not GENDER.search(titles.get(u) or "")


def _synthetic_job_url(page_url, title):
    """A distinct per-posting URL for the listing-page-only shapes below (FAQPage, FAQ accordion,
    title-only, bootstrap-panel), all of which have no separate detail page for any posting at all --
    every posting on the board previously got the SAME page_url as its row's source_url, so
    _post_inbox's dedupe-by-source_url (app/crawl.py) silently collapsed every posting on the board
    down to one row (confirmed live 2026-09-18: klinik-steger.de 5 -> 1, waldhausklinik.de 11 -> 1,
    klinik-bad-trissl.de 7 -> 1, klinik-wirsberg.de 2 -> 1). A "#<slug>" fragment is never sent to
    the server, so verify.py re-fetching this URL later still reads the same real page -- correct,
    since that page IS where this posting's own text lives. The slug is digit-free on purpose: a
    fragment containing a digit or "=" makes pflege_jobs.verify.FRAGMENT_URL force the render rung,
    which a synthetic, non-navigable fragment cannot satisfy."""
    slug = re.sub(r"[^a-zA-ZäöüÄÖÜß]+", "-", title or "").strip("-").lower()
    return page_url + "#" + (slug[:80] or "job")


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
                # An application FAQ ("Wie bewerbe ich mich?", "Was muss ich mitbringen?") on the
                # SAME career page uses this identical FAQPage shape but is not a posting at all --
                # accepting every Question with no job-shape gate skipped the sitemap/career-page
                # walk entirely on such a board (crawl_wp_jobs used to return early on any faq_rows),
                # losing every real posting (confirmed live 2026-09-18: karriere.klinikum-
                # altmuehlfranken.de, 6 real postings including 3 nursing, gone since commit 532de0f).
                if not title or not GENDER.search(title):
                    continue
                answer = (q.get("acceptedAnswer") or {}).get("text")
                u = _synthetic_job_url(cu_resp.url, title)
                j = {"title": title, "org": c["name"], "org_source": "seed",
                     "loc": [{"city": c.get("town"), "plz": None, "region": None}], "city_source": "seed",
                     "url": u, "page": cu_resp.url, "description": _txt(answer), "employmentType": None}
                out.append(row(host, u, j, "wp_jobs"))
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
        u = _synthetic_job_url(cu_resp.url, title)
        j = {"title": title, "org": c["name"], "org_source": "seed",
             "loc": [{"city": c.get("town"), "plz": None, "region": None}], "city_source": "seed",
             "url": u, "page": cu_resp.url, "description": _txt(bm.group(1)) if bm else None,
             "employmentType": None, "datePosted": _sane_date(date_posted)}
        out.append(row(host, u, j, "wp_jobs"))
    return out


# A whole class of small-clinic sites (own page builder each -- Elementor, Divi, plain TYPO3, no
# shared vendor at all) puts a posting's real title in a heading, then its real detail-page link a
# little further down as a generic "mehr erfahren"/"Details"/"Jetzt bewerben" button whose OWN
# anchor text carries no job signal at all -- neither JOB_PATH (the URL is a plain post slug, no
# job/stellen keyword) nor a gender marker on the link itself (confirmed live 2026-09-11:
# klinik-menterschwaige.de's Elementor loop-item cards). Keyed by a distinctive per-site heading
# regex rather than one shared fingerprint, since there is no shared vendor to detect generically --
# each entry names the one clinic it was found for.
INLINE_HEADING_SITES = {
    "klinik-menterschwaige.de": re.compile(r'<h3[^>]*class="[^"]*elementor-heading-title[^"]*"[^>]*>(.*?)</h3>', re.S),
}


def _inline_heading_job_rows(cu_resp, c, host):
    heading_rx = next((rx for domain, rx in INLINE_HEADING_SITES.items() if domain in host), None)
    if not heading_rx or not cu_resp or not cu_resp.ok:
        return []
    out = []
    base = _page_base(cu_resp)
    for hm in heading_rx.finditer(cu_resp.text):
        title = _txt(hm.group(1))
        if not title or not GENDER.search(title):
            continue
        chunk = cu_resp.text[hm.end():hm.end() + 4000]
        lm = re.search(r'href="([^"#]+)"', chunk)
        if not lm:
            continue
        url = urljoin(base, _html.unescape(lm.group(1)))
        j = {"title": title, "org": c["name"], "loc": [{"city": c.get("town"), "plz": None, "region": None}],
                 "city_source": "seed", "org_source": "seed",
             "url": url, "page": url, "description": _txt(chunk[:lm.start()]), "employmentType": None}
        out.append(row(host, url, j, "wp_jobs"))
    return out


# Some page builders (confirmed live 2026-09-11: klinik-bad-trissl.de, Divi) list postings as bare
# title text with NO link or description at all -- a JS tab widget re-renders the same title text
# once per department filter tab, so the raw page repeats each title several times.
TITLE_ONLY_SITES = {
    "klinik-bad-trissl.de": re.compile(r'et_pb_text_inner">\s*<p>([^<]+)</p>'),
}


def _title_only_job_rows(cu_resp, c, host):
    title_rx = next((rx for domain, rx in TITLE_ONLY_SITES.items() if domain in host), None)
    if not title_rx or not cu_resp or not cu_resp.ok:
        return []
    seen, out = set(), []
    for tm in title_rx.finditer(cu_resp.text):
        title = _txt(tm.group(1))
        if not title or not GENDER.search(title) or title in seen:
            continue
        seen.add(title)
        u = _synthetic_job_url(cu_resp.url, title)
        j = {"title": title, "org": c["name"], "loc": [{"city": c.get("town"), "plz": None, "region": None}],
                 "city_source": "seed", "org_source": "seed",
             "url": u, "page": cu_resp.url, "description": None, "employmentType": None}
        out.append(row(host, u, j, "wp_jobs"))
    return out


# A Bootstrap 3 "panel" accordion (confirmed live 2026-09-11: klinik-wirsberg.de) -- title in
# .panel-title > a, description in the matching .panel-body, same shared-URL/no-detail-page shape as
# the FAQPage/FAQ-accordion cases above but neither JSON-LD nor those exact class names. Not gated on
# a gender marker (unlike the other helpers): this site abbreviates some postings as "(VZ/TZ)" rather
# than "(m/w/d)", and being inside the page's own Stellenangebote accordion is confirmation enough.
BOOTSTRAP_PANEL_SITES = {"klinik-wirsberg.de"}
PANEL_TITLE_RX = re.compile(r'class="panel-title"><a[^>]*>(.*?)</a>', re.S)
PANEL_BODY_RX = re.compile(r'class="panel-body">(.*?)</div>\s*</div>\s*</div>', re.S)


def _bootstrap_panel_job_rows(cu_resp, c, host):
    if not any(d in host for d in BOOTSTRAP_PANEL_SITES) or not cu_resp or not cu_resp.ok:
        return []
    out = []
    parts = PANEL_TITLE_RX.split(cu_resp.text)[1:]  # alternating [title, tail, title, tail, ...]
    for title_html, tail in zip(parts[0::2], parts[1::2]):
        title = _txt(title_html)
        if not title:
            continue
        bm = PANEL_BODY_RX.search(tail)
        u = _synthetic_job_url(cu_resp.url, title)
        j = {"title": title, "org": c["name"], "loc": [{"city": c.get("town"), "plz": None, "region": None}],
                 "city_source": "seed", "org_source": "seed",
             "url": u, "page": cu_resp.url, "description": _txt(bm.group(1)) if bm else None,
             "employmentType": None}
        out.append(row(host, u, j, "wp_jobs"))
    return out


# A "dan-bewerbungen"-classed accordion (confirmed live 2026-09-22: kreisklinik-woerth.de, a legacy
# <font>-tag page from before this plugin's site was rebuilt) -- title in .../job-headline's own
# <b>, full Wir-suchen/Wir-sind/Wir-erwarten/... body in the matching .../job-body div right after,
# same shared-listing-page/no-detail-page shape as the FAQPage/FAQ-accordion/bootstrap-panel cases
# above. Each title repeats its own "(m/w/d) ()" suffix verbatim (an empty trailing placeholder in
# the plugin's own template) -- stripped, not left in a user-facing title.
DAN_BEWERBUNGEN_SITES = {"kreisklinik-woerth.de"}
DAN_HEADLINE_RX = re.compile(r'class="[^"]*dan-bewerbungen-job-headline[^"]*"[^>]*>.*?<b>(.*?)</b>', re.S)


def _dan_bewerbungen_job_rows(cu_resp, c, host):
    if not any(d in host for d in DAN_BEWERBUNGEN_SITES) or not cu_resp or not cu_resp.ok:
        return []
    out, seen = [], set()
    parts = DAN_HEADLINE_RX.split(cu_resp.text)[1:]  # alternating [title, tail, title, tail, ...]
    for title_html, tail in zip(parts[0::2], parts[1::2]):
        title = re.sub(r"\(\)\s*$", "", _txt(title_html) or "").strip()
        if not title or not GENDER.search(title) or title in seen:
            continue
        seen.add(title)
        u = _synthetic_job_url(cu_resp.url, title)
        j = {"title": title, "org": c["name"], "loc": [{"city": c.get("town"), "plz": None, "region": None}],
                 "city_source": "seed", "org_source": "seed",
             "url": u, "page": cu_resp.url, "description": _txt(tail[:4000]), "employmentType": None}
        out.append(row(host, u, j, "wp_jobs"))
    return out


# An Elementor "Toggle" (accordion) widget (confirmed live 2026-09-22: spezialklinik-neukirchen.de)
# -- a DIFFERENT Elementor widget than INLINE_HEADING_SITES' Heading widget, so a different class
# name and, unlike that one, no separate "mehr erfahren" href to find nearby: the toggle title
# itself is a JS-only <a> with no href at all, and the matching description sits in the immediately
# following .elementor-tab-content div. Most postings on this specific board have no gender marker
# at all (an admin/reception role, a doctor-leadership role, a flat "no, we don't train for X"
# non-opening) -- gating on GENDER here, same as every other listing-page-only helper, correctly
# keeps only the two that are real gendered postings instead of guessing which prose is a vacancy.
ELEMENTOR_TOGGLE_SITES = {"spezialklinik-neukirchen.de"}
TOGGLE_TITLE_RX = re.compile(r'class="elementor-toggle-title"[^>]*>(.*?)</a>', re.S)
TOGGLE_BODY_RX = re.compile(r'class="elementor-tab-content[^"]*"[^>]*>(.*?)</div>\s*</div>', re.S)


def _elementor_toggle_job_rows(cu_resp, c, host):
    if not any(d in host for d in ELEMENTOR_TOGGLE_SITES) or not cu_resp or not cu_resp.ok:
        return []
    out = []
    parts = TOGGLE_TITLE_RX.split(cu_resp.text)[1:]  # alternating [title, tail, title, tail, ...]
    for title_html, tail in zip(parts[0::2], parts[1::2]):
        title = _txt(title_html)
        if not title or not GENDER.search(title):
            continue
        bm = TOGGLE_BODY_RX.search(tail)
        u = _synthetic_job_url(cu_resp.url, title)
        j = {"title": title, "org": c["name"], "loc": [{"city": c.get("town"), "plz": None, "region": None}],
                 "city_source": "seed", "org_source": "seed",
             "url": u, "page": cu_resp.url, "description": _txt(bm.group(1)) if bm else None,
             "employmentType": None}
        out.append(row(host, u, j, "wp_jobs"))
    return out


def crawl_wp_jobs(c, session=None, towns=None):
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
        widen_url = _extbase_widen_limit_url(cu_resp.url, cu_resp.text) or _select_all_widen_url(cu_resp.url, cu_resp.text)
        if widen_url and widen_url != cu_resp.url:
            widened = get(widen_url, session=session)
            if widened and widened.ok:
                cu_resp = widened
    # One `seen` set shared across every _wp_job_rows call below (section, sitemap, career-page-link
    # stages) -- without it each stage's own fresh dedup set can't see a page (or a klinikum-jobs
    # widget page's own embedded postings, ALLJOBS_RX) another stage already fetched, and the same
    # job comes back as a duplicate row once per stage that happens to reach it.
    out, fetched, seen, titles = _BoardTotalRows(), set(), set(), {}
    if cu_resp and cu_resp.ok:
        not_a_job.add(_listing_page_key(cu_resp.url))
        # beesite (muz global-jobboard-client): fingerprinted on the page already fetched here, no
        # extra request -- its own listing is JS-injected, so the walk below would find nothing.
        # Never a registry label (see pflege_jobs.sources.beesite docstring, TASK-40 AC#3).
        from pflege_jobs.sources.beesite import is_beesite, crawl_beesite
        if is_beesite(cu_resp):
            return crawl_beesite(c, session=session)
        # Same probe-and-delegate contract as beesite: three more engines whose listing exists only
        # in a place the generic sitemap/anchor walk cannot see -- a same-origin JSON search API
        # (asklepios), the page's own embedded job JSON behind a handlebars template (eRecruiter),
        # and a widget loader that names its tenant board (concludis). All three are fingerprinted
        # on the response already fetched above, so the probe itself costs no extra request.
        for delegate in (crawl_asklepios, crawl_erecruiter, crawl_concludis_widget):
            rows = delegate(c, session=session, cu_resp=cu_resp)
            # `if rows:` alone is falsy for an empty-but-annotated _BoardTotalRows, so a board this
            # delegate DID recognise (it parsed a total off the board's own JSON) but read zero rows
            # from would fall through as an untagged empty list -- exactly the under-read shape this
            # mechanism exists to catch (review finding, 2026-09-22: 0 rows vs a declared total>0).
            # board_total is not None means "this vendor, and it told us its total", distinct from
            # "not this vendor" (plain [] / board_total still None) which must keep falling through.
            if rows or getattr(rows, "board_total", None) is not None:
                return rows
        # Merged into the normal walk below, not returned early: these shapes have no per-job
        # detail link for the sitemap/career-page walk to find on its own, but a career page can
        # ALSO carry real, separately-discoverable postings alongside them -- returning early here
        # used to skip the sitemap/career-page walk entirely (confirmed live 2026-09-18:
        # karriere.klinikum-altmuehlfranken.de lost all 6 real postings, 3 nursing, to an unrelated
        # application FAQ on the same page taking this branch first).
        faq_rows = (_faqpage_job_rows(cu_resp, c, host) or _faq_accordion_job_rows(cu_resp, c, host)
                    or _inline_heading_job_rows(cu_resp, c, host) or _title_only_job_rows(cu_resp, c, host)
                    or _bootstrap_panel_job_rows(cu_resp, c, host) or _dan_bewerbungen_job_rows(cu_resp, c, host)
                    or _elementor_toggle_job_rows(cu_resp, c, host))
        if faq_rows:
            out = _BoardTotalRows(faq_rows)   # enriched once, together with everything else, at the final return
            fetched = {j["payload"]["url"] for j in out}
    section_label, section_url = _wp_nursing_section_url(cu, cu_resp)
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
                        if not _listing_dup(u, not_a_job, titles)]
        if section_urls:
            # += / |=, not = -- out/fetched may already carry the FAQ-shape rows merged in above.
            new = _wp_job_rows(section_urls, c, host, session,
                               section_labels=[section_label] if section_label else None, seen=seen, titles=titles, towns=towns)
            out += new
            fetched |= {j["payload"]["url"] for j in new}

    sitemap_urls = find_job_urls(base, session=session)
    if not sitemap_urls and cu_resp and cu_resp.ok:
        # TASK-85 AC#1: sitemap discovery (and, since AC#2, the wp-json CPT fallback inside
        # find_job_urls) found NOTHING -- whatever the career page's own homepage/pagination links
        # below turn up instead is a degraded read of this board, not a complete one. This used to be
        # a stderr-only print with the crawl still returning a clean, nonzero-row success (confirmed
        # live: karriere.ge-passau.de reported "adapter: 2 rows" as a clean result while 27 real
        # sitemap job urls sat behind the untried hyphenated /sitemap-index.xml). A caller that reads
        # this attribute (see _BoardTotalRows) can record it as its own crawl_issue instead of never
        # learning the count it got was not the real board.
        out.degraded = "sitemap_and_wp_json_empty"
    urls = [u for u in sitemap_urls if not _listing_dup(u, not_a_job, titles)]
    if urls:
        more_urls = [u for u in urls if u not in fetched]
        new = _wp_job_rows(more_urls, c, host, session, seen=seen, titles=titles, towns=towns)
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
    iframe_url = _iframe_start_url(cu_resp)
    if iframe_url:
        page_urls += _paginated_job_links(iframe_url, session=session, titles=titles)
    page_urls += _widget_endpoint_job_links(cu_resp, session=session)
    page_urls = [u for u in dict.fromkeys(page_urls) if not _listing_dup(u, not_a_job, titles) and u not in fetched]
    if page_urls:
        new = _wp_job_rows(page_urls, c, host, session, seen=seen, titles=titles, towns=towns)
        out += new
        fetched |= {j["payload"]["url"] for j in new}

    if not out:
        # hr4you: the career page names no vendor markup at all (its tenant links sit on a
        # location/jobs subpage the page itself points to, not on the page or its own scripts) --
        # tried last, after every generic sitemap/page-link path above found nothing. Never a
        # registry label either (see pflege_jobs.sources.hr4you docstring, TASK-40 AC#3).
        # crawl_hr4you returns a plain list, not a _BoardTotalRows -- assigning it straight to `out`
        # would silently drop the .degraded tag set above (review finding, 2026-09-22: a board whose
        # sitemap AND wp-json are both empty, and that hr4you then also reads 0 rows from, is exactly
        # the worst case AC#1 exists to flag, and it lost the tag here).
        from pflege_jobs.sources.hr4you import crawl_hr4you
        degraded = out.degraded
        out = _BoardTotalRows(crawl_hr4you(c, session=session))
        out.degraded = degraded
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


def _page_meta_date(htmltext):
    """A page's own publish date read directly off an already-fetched response: the
    article:modified_time / og:updated_time <meta> every WP SEO plugin (Yoast, RankMath) stamps on
    each post, InnKlinikum's own "Ausschreibung ... vom" body text, or a bare schema.org
    itemprop="datePosted" meta some TYPO3 boards use instead of JSON-LD -- in that order. Shared by
    parse_job_page (reads it from the SAME response it is already parsing, TASK-85 AC#6) and
    _enrich_wp_fallback_fields (its fallback for a row parse_job_page could not reach this from, e.g.
    a sitemap/wp-json url _wp_job_rows fetched under a different code path)."""
    dm = WP_META_DATE_RX.search(htmltext or "")
    if dm:
        return _sane_date(dm.group(1))
    am = AUSSCHREIBUNG_DATE_RX.search(htmltext or "")
    if am:
        return _sane_date("%s-%02d-%02d" % (am.group(3), int(am.group(2)), int(am.group(1))))
    im = ITEMPROP_DATE_RX.search(htmltext or "")
    if im:
        return _sane_date(im.group(1))
    return None


def _enrich_wp_fallback_fields(rows, session=None):
    """crawl_wp_jobs's own parser (owned by TASK-35) extracts no employmentType -- backfill it here
    from Vollzeit/Teilzeit named in the visible body (already sitting in payload.description, no
    extra fetch). datePosted is normally filled by parse_job_page itself now (_page_meta_date, off the
    SAME response already fetched) -- this re-fetch is only reached for the rare row that got here
    without ever going through parse_job_page. TASK-85 AC#6: this used to be the ONLY place any of
    these three regexes ran, so a JSON-LD-less board (AMEOS TYPO3: itemprop only, no JobPosting block)
    re-fetched every single one of its ~778 detail pages a second time just to find a date the first
    fetch already had in hand -- serialized, ~doubling total requests, one run killed past 17 minutes."""
    for r in rows:
        p = r["payload"]
        if not p.get("employmentType"):
            m = EMPLOYMENT_KEYWORD_RX.search(p.get("description") or "")
            if m:
                p["employmentType"] = m.group(1).title()
        if not p.get("datePosted"):
            resp = get(p.get("url") or r["source_url"], session=session)
            if resp and resp.ok:
                p["datePosted"] = _page_meta_date(resp.text)
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
            j["loc"] = [{"city": c["town"], "plz": None, "region": None}]; j["city_source"] = "seed"
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


def crawl_mein_check_in(c, session=None):
    """mein-check-in: resolve the tenant slug, then read /<tenant>/overview.

    The clinic's own careers page is usually a thin wrapper that links to
    `mein-check-in.de/<tenant>/index`; the listing lives on that host and is server-rendered, with
    each vacancy as `position-<id>`. Titles are already in the listing anchors, so the detail fetch
    is only needed for the description plus the JobPosting microdata (datePosted, employmentType,
    per-job address) -- keeping the crawl to ~1 request per job. No cap on job count -- the one
    already-fetched overview page's own regex matches are the only stop (this was the last surviving
    VENDOR_MAX_JOBS=300 default; crawl_rexx already dropped its own copy of the same cap after it
    silently truncated a 330-posting board).
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
        # MCI_ITEMPROP has no organization field at all (no hiringOrganization microdata on this
        # theme) -- org is always the caller's seed clinic name, and city defaults to the seed town
        # until/unless the per-job addressLocality below overrides it.
        j = {"title": title, "org": c["name"], "org_source": "seed",
             "loc": [{"city": c.get("town"), "plz": None, "region": None}], "city_source": "seed",
             "url": u, "page": u, "description": None,
             "section_labels": [pid_group[pid]] if pid_group.get(pid) else []}
        d = get(u, session=session)
        if d and d.ok:
            full = parse_job_page(d.text, u, c["name"])
            if full and full.get("description"):
                j["description"] = full["description"]
            meta = _mci_job_meta(d.text)
            j["datePosted"] = _sane_date(meta["datePosted"])
            j["employmentType"] = meta["employmentType"]
            if meta["addressLocality"]:                   # source gives the real per-job branch
                # A crawler may not assert a region it did not read -- the "or 'BAYERN'" fallback
                # was the one fabricated-region writer that survived the 2026-09-16 cleanup
                # (missed because it wrote the value at runtime, not as a literal in this file).
                j["loc"] = [{"city": meta["addressLocality"], "plz": meta["postalCode"],
                             "region": meta["addressRegion"] or None}]
                j["city_source"] = "page"
        out.append(row(host, u, j, "mein-check-in"))
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------------------
# asklepios: Next.js career portal, own POST /api/search
# ---------------------------------------------------------------------------
# www.asklepios.com/karriere/jobs renders zero job markup server-side, but the search its own client
# runs is a plain JSON POST to same-origin /api/search, and the per-tenant search id it posts sits in
# the careers page's own HTML ("restEndpoint"). Probed live 2026-09-21: 1398 postings, 90 of them at
# the 7 Bavarian Asklepios sites. The list response already carries every field a row needs (title,
# company, location, plz, workarea, working time, publication date, public jobLink on
# karriere.asklepios.com) -- unlike SmartRecruiters there is no separate per-posting detail endpoint
# to fetch, so the whole board costs one POST per page.
ASKL_ENDPOINT_RX = re.compile(r'"restEndpoint"\s*:\s*"(/\.rest/search/job/[^"]+)"')
# The portal clamps its own page size: an l=200 request answers with 60 items (confirmed live), so
# paging is driven by the server's `count` and by ids running out, never by a ceiling of ours.
ASKL_PAGE = 60
ASKL_DATE_RX = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2})")


def post_json(u, payload, timeout=30, session=None):
    """POST sibling of get() -- same session attempt/ok tally (TASK-72 AC#1)."""
    if session is not None:
        session._attempts = getattr(session, "_attempts", 0) + 1
    try:
        r = (session or requests).post(u, json=payload, headers=dict(H, **{"Content-Type": "application/json"}),
                                       timeout=timeout)
    except Exception:
        return None
    if session is not None and r.ok:
        session._ok = getattr(session, "_ok", 0) + 1
    return r


def _askl_date(raw):
    """publicationDate is German short-form "18.09.26, 00:00", not ISO."""
    m = ASKL_DATE_RX.match((raw or "").strip())
    return _sane_date("20%s-%s-%s" % (m.group(3), m.group(2), m.group(1))) if m else None


def crawl_asklepios(c, session=None, cu_resp=None):
    cu = (c.get("careers_url") or "").strip()
    r = cu_resp if (cu_resp is not None and cu_resp.ok) else (get(cu, session=session) if cu else None)
    m = ASKL_ENDPOINT_RX.search(r.text) if r and r.ok else None
    if not m:
        return []
    p = urlparse(r.url)
    api = "%s://%s/api/search" % (p.scheme, p.netloc)
    out, seen, offset = [], set(), 0
    while True:
        resp = post_json(api, {"searchEndpoint": m.group(1), "q": "", "o": offset,
                               "l": ASKL_PAGE, "f": False, "filter": {}}, session=session)
        if not resp or not resp.ok:
            break
        try:
            data = resp.json()
        except ValueError:
            break
        items = data.get("items") or []
        # The board's own end: either its stated count is reached, or a page brings no id we have
        # not already read. No offset ceiling.
        fresh = [it for it in items if str(it.get("id")) not in seen]
        if not fresh:
            break
        seen.update(str(it.get("id")) for it in fresh)
        for it in fresh:
            title, url = _txt(it.get("title"), 300), it.get("jobLink")
            if not (title and url):
                continue
            out.append(row(p.netloc, url,
                           {"title": title, "org": it.get("company") or c["name"],
                            "org_source": None if it.get("company") else "seed",
                            "loc": [{"city": it.get("location"), "plz": it.get("plz"), "region": None}],
                            "url": url, "page": r.url,
                            "description": _txt(it.get("qualifications")),
                            "datePosted": _askl_date(it.get("publicationDate")),
                            "employmentType": it.get("workingTime"),
                            "section_labels": list(it.get("workareas") or [])},
                           "asklepios"))
        offset += len(items)
        count = data.get("count")
        if count is not None and offset >= count:
            break
        time.sleep(0.5)
    return out


# ---------------------------------------------------------------------------
# eRecruiter: <jobs-subdomain>/Jobs, full job list embedded in the page
# ---------------------------------------------------------------------------
# The listing looks client-side -- the markup around it is a handlebars template whose plain-HTML
# form still carries the literal "/Job/{{Id}}" placeholder, so no anchor matches JOB_PATH -- but the
# data the template renders is already in the page: `window.jobList = new JobList($list, $template,
# {...})`, whose third argument is the whole board as JSON. No render, no AJAX. Confirmed live
# 2026-09-21 on jobs.bezirkskliniken-schwaben.de (57) and jobs.klinikum-ab-alz.de (62); on both,
# TotalJobsCount equalled the embedded list length and a ?page=2 request returned the identical
# list, i.e. the engine ships the whole board at once and has no server-side page to walk.
ERECRUITER_LIST_RX = re.compile(r"new\s+JobList\s*\(.*?,\s*(?=\{)", re.S)


def _erecruiter_jobs(htmltext):
    """(jobs, board) -- board is the whole embedded JobList payload (TotalJobsCount, Pagination, ...),
    not only the Jobs array, so a caller can read the engine's own completeness signal too."""
    m = ERECRUITER_LIST_RX.search(htmltext or "")
    if not m:
        return [], {}
    try:
        data, _ = json.JSONDecoder().raw_decode(htmltext, m.end())
    except ValueError:
        return [], {}
    return [j for j in (data.get("Jobs") or []) if isinstance(j, dict)], data


def _erecruiter_host_resp(cu_resp, session=None):
    """The registered careers_url is sometimes only a WRAPPER page that links out to the real board
    on a same-domain subdomain, never embedding the JobList JSON itself -- confirmed live 2026-09-21:
    klinikum-ab-alz.de/karriere/ links jobs.klinikum-ab-alz.de/Jobs (63 postings, TASK-85 AC#5, the
    audit misread this as a Knockout SPA needing a render rung -- it is this same engine one hop
    away); bezirkskliniken-schwaben.de/ausbildung-karriere/... links jobs.bezirkskliniken-schwaben.de/
    Jobs (56 postings, AC#4, the audit's "inline JSON model" -- also this engine, also one hop away).
    Neither link matches JOB_PATH (a bare "/Jobs", no trailing slash) or carries gender-marked anchor
    text, so the generic job-link scan never follows either. Same "resolve the real tenant host
    first" shape as crawl_dvinci's dvinci_host, narrowed to the SAME registrable domain the caller
    already trusts -- a genuinely different domain is _job_link_pairs' repeat-count call, not this
    one's (see its docstring)."""
    if not cu_resp or not cu_resp.ok or ERECRUITER_LIST_RX.search(cu_resp.text):
        return cu_resp
    own_netloc = urlparse(cu_resp.url).netloc
    own_domain = _registrable_domain(own_netloc)
    tried = set()
    hrefs = [h for h in re.findall(r'href="(https?://[^"#]+)"', cu_resp.text, re.I) if JOB_SITEMAP.search(h)]
    for h in sorted(dict.fromkeys(hrefs), key=len):
        p = urlparse(h)
        if p.netloc == own_netloc or p.netloc in tried or _registrable_domain(p.netloc) != own_domain:
            continue
        tried.add(p.netloc)
        r = get(h, session=session)
        if r and r.ok and ERECRUITER_LIST_RX.search(r.text):
            return r
    return cu_resp


class _BoardTotalRows(list):
    """A plain row list -- the return-value shape app/crawl.py's generic vendor-adapter caller
    depends on, so this class changes nothing about how a caller sees it -- that also carries THIS
    call's own board-total signal as instance attributes.

    Not stashed on `session` (the first cut did, mirroring get()'s _attempts/_ok side channel):
    session is one per whole RUN (app/crawl.py:655 creates a single requests.Session and reuses it
    board after board), while a board total is per-BOARD. get()'s _attempts/_ok survive that reuse
    only because _fetch_board resets them right before every board fetch (app/crawl.py:707); nothing
    equivalent reset _board_total, so it kept the last eRecruiter board's number and _fetch_board's
    pending AC#2 wiring ('read session._board_total after calling a vendor adapter') would have read
    a stale total for every following NON-eRecruiter board and recorded it as falsely incomplete
    (review finding, 2026-09-22). A fresh instance of this class per call carries only this call's
    own value -- nothing to go stale, nothing another file has to remember to reset."""
    board_total = None
    board_paginated = None
    # None: a normal, complete read. A short string names WHY this read is degraded (currently only
    # crawl_wp_jobs' "sitemap_and_wp_json_empty", TASK-85 AC#1) -- a caller can record it as its own
    # crawl_issue instead of a degraded-but-nonzero row count silently reading as a clean success.
    degraded = None


def crawl_erecruiter(c, session=None, cu_resp=None):
    cu = (c.get("careers_url") or "").strip()
    r = cu_resp if (cu_resp is not None and cu_resp.ok) else (get(cu, session=session) if cu else None)
    r = _erecruiter_host_resp(r, session=session)
    if not (r and r.ok):
        # A bare [] here has no .board_total -- app/crawl.py's pending wiring (see _BoardTotalRows'
        # docstring) reads that attribute unconditionally and would AttributeError on this path
        # (review finding, 2026-09-22). _BoardTotalRows' class-level board_total=None default makes
        # an empty instance exactly as safe to read as a real one that found no total.
        return _BoardTotalRows()
    p = urlparse(r.url)
    base = "%s://%s" % (p.scheme, p.netloc)
    out = _BoardTotalRows()
    jobs, board = _erecruiter_jobs(r.text)
    # TASK-88: both live tenants today ship TotalJobsCount == len(Jobs) (the comment above says why --
    # no server-side page to walk), so this has never been WRONG yet, but nothing ever checked it
    # either -- a third tenant that DOES paginate server-side would silently under-read as a clean
    # success. This fn returns a plain row list whose shape the generic vendor-adapter caller depends
    # on, so board-level metadata (not per-row) has no return-value channel of its own besides
    # attributes on the list instance itself (see _BoardTotalRows).
    total = board.get("TotalJobsCount")
    out.board_total = total if isinstance(total, int) else None
    out.board_paginated = bool((board.get("Pagination") or {}).get("IsPagination"))
    for j in jobs:
        jid = j.get("Id")
        title = _txt(j.get("Title"), 300)
        if jid is None or not title:
            continue
        u = "%s/Job/%s" % (base, jid)
        d = get(u, session=session)
        # The detail page carries a full schema.org JobPosting; the embedded list row is still the
        # better title (the JSON-LD repeats it) and the only source for Location/SubTitle, so the
        # detail is used for description/dates and the list row for identity.
        full = parse_job_page(d.text, d.url, c["name"]) if d and d.ok else None
        loc = (full or {}).get("loc") or [{}]
        city = loc[0].get("city") or _txt(j.get("Location"), 200)
        # This engine puts its own internal contract code ("1", "2") in schema.org's employmentType
        # slot -- meaningless outside its database, so drop it and let _enrich_wp_fallback_fields
        # read the real Voll-/Teilzeit wording out of the text the board does publish.
        et = (full or {}).get("employmentType")
        out.append(row(p.netloc, u,
                       {"title": title, "org": (full or {}).get("org") or c["name"],
                        "org_source": (full or {}).get("org_source", "seed"),
                        "loc": [{"city": city, "plz": loc[0].get("plz"), "region": loc[0].get("region")}],
                        "url": u, "page": r.url,
                        "description": " ".join(x for x in (_txt(j.get("SubTitle")), (full or {}).get("description")) if x) or None,
                        "datePosted": (full or {}).get("datePosted") or _erecruiter_date(j.get("Date")),
                        "employmentType": None if (et or "").strip().isdigit() else et},
                       "erecruiter"))
        time.sleep(0.5)
    return _enrich_wp_fallback_fields(out, session=session)


ERECRUITER_DATE_RX = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})")


def _erecruiter_date(raw):
    m = ERECRUITER_DATE_RX.match((raw or "").strip())
    return _sane_date("%s-%s-%s" % (m.group(3), m.group(2), m.group(1))) if m else None


# ---------------------------------------------------------------------------
# concludis job-board widget: <tenant>.concludis.de/prj/lst/?b=<board>
# ---------------------------------------------------------------------------
# The "concludis" census label mostly sits on WordPress career sites (handled by crawl_wp_jobs), but
# a real tenant embeds concludis' own widget instead: the clinic's page ships an empty container and
# a loader script that names the tenant host and board id, then pulls the list over AJAX. Those two
# values are the only tenant-specific facts needed -- /prj/lst/?b=<board>&jsinclude=1 answers the
# complete list to a plain GET (confirmed live 2026-09-21 on swmbrk.concludis.de board 36: 18 of 18
# postings, the same count the widget's own "18 Stellen gefunden" header states, and ?page=2 returns
# the identical list). Each posting's own detail page carries a schema.org JobPosting once asked for
# the jsinclude fragment; without that parameter it 302s away.
CONCLUDIS_WIDGET_HOST = re.compile(r"\(\s*window\s*,\s*document\s*,\s*'script'\s*,\s*'concludis'\s*,\s*'([a-z0-9.\-]+)'\s*\)", re.I)
CONCLUDIS_BOARD = re.compile(r"concludis\(\s*'setJobBoard'\s*,\s*'([^']+)'\s*\)", re.I)
CONCLUDIS_JOB = re.compile(r"cJobboard\.openJob\('([^']+)'\).*?<span class=\"headerlink stellenlink\">(.*?)</span>", re.S)


def concludis_widget(careers_html):
    """-> (tenant_host, board_id) named by the clinic page's own loader script, or (None, None)."""
    h, b = CONCLUDIS_WIDGET_HOST.search(careers_html or ""), CONCLUDIS_BOARD.search(careers_html or "")
    return (h.group(1), b.group(1)) if h and b else (None, None)


def crawl_concludis_widget(c, session=None, cu_resp=None):
    cu = (c.get("careers_url") or "").strip()
    r = cu_resp if (cu_resp is not None and cu_resp.ok) else (get(cu, session=session) if cu else None)
    host, board = concludis_widget(r.text) if r and r.ok else (None, None)
    if not host:
        return []
    lst = get("https://%s/prj/lst/?b=%s&lang=de_DE&jsinclude=1" % (host, board), session=session)
    if not (lst and lst.ok):
        return []
    out = []
    for href, inner in CONCLUDIS_JOB.findall(lst.text):
        title = _txt(inner, 300)
        u = urljoin(lst.url, _html.unescape(href))
        if not title:
            continue
        sep = "&" if "?" in u else "?"
        d = get(u + sep + "jsinclude=1", session=session)
        full = parse_job_page(d.text, u, c["name"]) if d and d.ok else None
        loc = (full or {}).get("loc") or [{}]
        out.append(row(host, u,
                       {"title": (full or {}).get("title") or title, "org": (full or {}).get("org") or c["name"],
                        "org_source": (full or {}).get("org_source", "seed"),
                        "loc": [{"city": loc[0].get("city"), "plz": loc[0].get("plz"), "region": loc[0].get("region")}],
                        "url": u, "page": r.url,
                        "description": (full or {}).get("description"),
                        "datePosted": (full or {}).get("datePosted"),
                        "employmentType": (full or {}).get("employmentType")},
                       "concludis-widget"))
        time.sleep(0.5)
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
    # jobPublicationURL sometimes carries a trailing /<slug> after the numeric id and sometimes
    # doesn't (confirmed live 2026-09-22: Bamberg/Fuerth/Neumarkt each store 5 of the SAME job twice,
    # once per shape) -- the id-only form is itself a real, currently-serving dvinci URL (dvinci 200s
    # it and redirects straight to the slugged page), so normalize to it here rather than downstream:
    # one shape stored, never two rows for one job.
    url = re.sub(r"(/de/jobs/\d+)/[^/?#]+", r"\1", url)
    employment_type = ", ".join(wt.get("name") for wt in (jo.get("workingTimes") or []) if wt.get("name")) or None
    company_name = (jo.get("company") or {}).get("name") or None
    return {"title": j.get("position"), "org": company_name or org,
            "org_source": None if company_name else "seed",
            "loc": [{"city": city, "plz": addr.get("zipCode"), "region": None}],
            "url": url, "page": url,
            # createdDate (posting date) not startDate (Eintrittsdatum/job start date) -- startDate is
            # null on most postings anyway, but the field itself is the wrong signal for "date posted".
            "datePosted": _sane_date(jo.get("createdDate")),
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
            p["loc"] = [{"city": c["town"], "plz": None, "region": None}]; p["city_source"] = "seed"
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
     # (Prinzregentenstraße 18, plz 80538) -- never the real work site of any of its 32 Bavaria
     # clinics (confirmed live 2026-09-11 across 3 sampled postings, different sub-brands, same
     # address every time; still true 2026-09-18). crawl_group_portal must never carry that address
     # through as the posting's own location -- see hq_location_untrusted below.
     "hq_location_untrusted": True,
     # ...but each detail page DOES carry a structured "Einsatzort" block naming the real kbo site
     # and its street address (confirmed live 2026-09-21: present on all 108 job pages on the
     # board). That block is the posting's own site of work -- both its city/postcode and the site
     # NAME, which is the one kbo signal that tells two same-town sister sites apart (Lech-Mangfall
     # vs Heckscher in Landsberg am Lech, both on this board, both in the registry). Group 1 is the
     # site name, group 2 its address block.
     "site_block_rx": re.compile(r"job__related-site-header.*?<h2[^>]*>(.*?)</h2>"
                                 r".*?job__related-site-description[^>]*>(.*?)</p>", re.S),
     # The real site is only named in the title's own trailing "in <Ort>" / "am Standort <Ort>" /
     # "des Standorts <Ort>" text, when present at all -- best-effort, not every posting names one
     # (e.g. a bare "Pflegefachhelfer (m/w/d)" carries no location clue anywhere). The capture is
     # validated against the towns list before use (crawl_group_portal) -- a non-place capture like
     # "Oberbayern" or a stray adjective must not become the posting's city.
     "title_city_rx": re.compile(r"(?:\bin\b|am Standort|des Standorts)\s+([A-ZÄÖÜ][\wäöüß.\-]*"
                                  r"(?:\s+(?:an|am|a\.\s?d\.|i\.\s?d\.)\s+[\wäöüß.\-]+)?"
                                  r"(?:\s+[A-ZÄÖÜ][\wäöüß.\-]*){0,2})$")},
    # Barmherzige Brüder run one board for all their Bavarian houses.
    {"match": r"barmherzige", "own_ok": r"karriere\.barmherzige\.net", "list": "https://karriere.barmherzige.net/jobs/",
     "page_param": "c_page",
     "job_rx": r"https://karriere\.barmherzige\.net/jobs/[a-z0-9][^\"'\s>?]+", "host": "karriere.barmherzige.net"},
]


# TASK-99 (2026-09-22): a DIFFERENT shape than GROUP_PORTALS above -- these clinics each keep their own,
# genuinely distinct careers_url (crawlers.routing.plan() correctly treats them as separate boards to
# fetch), but the underlying recruiting-vendor ACCOUNT behind those URLs is shared, so whichever clinic's
# crawl fires returns the WHOLE account's postings, not just its own. app/crawl.py._vendor_rows tags every
# row with board_clinic_ids = the triggering board's own clinic list, which is a single clinic here --
# pflege_jobs.registry.Matcher._match_board then correctly refuses a single-clinic pool when the posting's
# own city disagrees (decision-5), so nothing outside that one clinic can ever match. account_pool_for()
# widens the pool to every sibling BEFORE the matcher sees it.
# Manually curated on purpose, not auto-detected from the vendor's account id: the two confirmed instances
# below took a live Matcher replay plus (for Gesundheitswelt) confirming the sibling by name in the
# portal's own site/location filter -- auto-clustering by a resolved vendor ident risks silently grouping
# unrelated clinics that merely share a SaaS reseller, with no such verification step.
VENDOR_ACCOUNT_POOLS = [
    # SmartRecruiters "ArtemedSE" company feed: 7 distinct own-domain careers_urls, one shared candidate
    # pool. Verified live 2026-09-22: replaying pflege_jobs.registry.Matcher with this pool resolves
    # 261 of 303 previously-unmatched jobs.smartrecruiters.com inbox rows via the existing R0_board_town
    # rule alone -- no new matching logic. 18813/18872 is the Feldafing Plan-KH/Vertrags-KH twin pair.
    {"account": "SmartRecruiters/ArtemedSE",
     "clinic_ids": ["16228", "16235", "18105", "18802", "18808", "18813", "18872", "76108"]},
    # karriere.gesundheitswelt.de: confirmed live 2026-09-22 -- the portal's own site/location filter on
    # https://karriere.gesundheitswelt.de/stellenangebote.html lists "Simssee Klinik GmbH" (18713, Bad
    # Endorf) as one of its own options alongside St. Irmingard (18721, Prien am Chiemsee), even though
    # 18713's OWN registered careers_url is a separate, unrelated (and walled) domain. Other towns the
    # portal also lists (Rosenheim, Seeon-Seebruck) matched no Krankenhausplan-registered clinic at
    # that town -- likely non-hospital Gesundheitswelt facilities, out of this registry's scope (same
    # class as TASK-103), not added here.
    {"account": "Gesundheitswelt Chiemgau (karriere.gesundheitswelt.de)", "clinic_ids": ["18721", "18713"]},
    # TASK-118: 19001 (Krankenhaus Schongau) and 19002 (Krankenhaus Weilheim) share one IDENTICAL
    # careers_url (meinkrankenhaus2030.de/karriere/stellenboerse) -- crawlers.routing._boards() already
    # groups them correctly on a full-registry crawl (same exact URL). The gap is scope: a CLINIC-
    # scoped crawl (scope=clinic, value=19001 alone) never includes 19002 in plan["clinics"] at all, so
    # _boards() only ever sees ids=["19001"], and app/crawl.py._vendor_rows' single-clinic seed-city
    # fallback then stamps EVERY row on the shared board with Schongau -- including postings whose own
    # body text plainly says "am Standort Weilheim" (no JSON-LD on this board at all; confirmed live
    # 2026-09-23, posting_id 6268's real page). Same mechanism as the two entries above (this list
    # widens `ids` in _vendor_rows regardless of how narrow the crawl's own scope was), just a
    # same-URL pair rather than a shared-account/different-URL one.
    {"account": "meinkrankenhaus2030.de (Weilheim-Schongau)", "clinic_ids": ["19001", "19002"]},
]


def account_pool_for(clinic_id):
    """The full sibling clinic_id list (as strings) for clinic_id's shared vendor account, or None if it
    is not part of one. Only ever WIDENS a board's own clinic list -- never used to route a fetch."""
    clinic_id = str(clinic_id)
    for p in VENDOR_ACCOUNT_POOLS:
        if clinic_id in p["clinic_ids"]:
            return p["clinic_ids"]
    return None


# TASK-102 (2026-09-22): kliniken-nordoberpfalz.talention.com's own JSON-LD jobLocation.addressLocality is
# filled inconsistently by whoever posted each job on the employer's side -- confirmed live: one posting
# carries the clean "Weiden, Bayern, Deutschland", another for the SAME site carries the facility/department
# label "Klinikum Weiden Zentrale Notaufnahme" in the exact same field, same page structure. No other,
# cleaner field exists on the page to prefer instead -- this is upstream data-entry inconsistency, not a
# parser bug. Vendor-specific (ats_type == "talention" only) and pool-scoped (only ever picks a town this
# board's OWN registry pool already contains), same shape as GROUP_PORTALS' kbo Einsatzort handling for a
# different vendor (TASK-57) -- never invents a town the board doesn't already know about.
# TASK-81 mechanism #1: la-regio-kliniken.de/stellenportal shares one board between 26108 (LA-Regio
# Kliniken Landshut, the 862-bed general hospital) and 26103 (Kinderkrankenhaus St. Marien Landshut,
# 120-bed, pediatric-only). The board is a JS SPA with no static per-posting location/department field
# at all (confirmed live 2026-09-22, no Einsatzort-style block anywhere in the fetched HTML) -- every
# posting currently lands on whichever clinic's board_clinic_ids happened to be first, which today means
# all 39 open postings sit on 26103 including clearly general-hospital departments (Gastroenterologie,
# Kardiologie, Onkologie, Anästhesie, IMC) a 120-bed children's hospital does not run on its own. The one
# real signal is the posting's own title: a pediatric nursing qualification/ward always says so verbatim
# ("Kinderkrankenpflegekräfte", "(Kinder-)", "Kinderchirurgische", "Pädiatrie") -- checked against all 39
# live titles, zero false positives either direction.
LA_REGIO_LANDSHUT_PEDIATRIC_RX = re.compile(r"\bkinder|p[aä]diatrie", re.I)


def split_la_regio_landshut(title):
    return "26103" if LA_REGIO_LANDSHUT_PEDIATRIC_RX.search(title or "") else "26108"


def clean_talention_city(raw_city, pool_towns):
    """raw_city already naming exactly one of pool_towns (as a whole word) -> that clean town name.
    Zero or more than one hit (e.g. "Krankenhaus Tirschenreuth und Klinikum Weiden" names two) -> raw_city
    unchanged, so town matching fails safely exactly as it did before (no match beats a wrong match)."""
    if not raw_city:
        return raw_city
    towns = [t for t in (pool_towns or []) if t]
    if not towns:
        return raw_city
    rx = re.compile(r"\b(" + "|".join(re.escape(t) for t in towns) + r")\b", re.I)
    hits = {m.group(1).lower() for m in rx.finditer(raw_city)}
    if len(hits) == 1:
        hit = next(iter(hits))
        return next(t for t in towns if t.lower() == hit)
    return raw_city


def extract_standort_city(description, towns):
    """A board with no structured location field at all (no JSON-LD, no icon-fact -- confirmed live
    2026-09-23, meinkrankenhaus2030.de, TASK-118) can still state the real work site in plain body
    prose ("...suchen wir zum naechstmoeglichen Zeitpunkt eine/n ... am Standort Weilheim"). Only
    accept a hit that is itself a real, known town (the same "no match beats a wrong match" bar
    clean_talention_city uses) -- `towns` is the full registry town set here, not one board's own
    pool, so the match is still gated by _match_board's own town rung finding that town among the
    board's actual clinics; this function only has to avoid inventing a town that does not exist."""
    if not description or not towns:
        return None
    ordered = sorted((t for t in towns if t), key=len, reverse=True)
    if not ordered:
        return None
    rx = re.compile(r"\bStandort:?\s+(" + "|".join(re.escape(t) for t in ordered) + r")\b", re.I)
    m = rx.search(description)
    # `towns` (app.data.towns()) is norm_text()-lowercased; keep the page's own capitalisation for
    # the value actually stored (e.g. "Weilheim", not "weilheim") once the lowercase form is confirmed
    # to be a real registry town.
    return m.group(1) if m and m.group(1).lower() in towns else None


def group_portal_for(c):
    """A clinic whose own NAME (or, historically, careers_url) merely shares a word with a group's
    match pattern must not lose its own working board to that group -- "barmherzige" alone matched
    both the unrelated Barmherzige Bruder order's own boards (they run no shared portal at all) and
    a same-named-but-distinct microsite (barmherzige-bieten-zukunft.de), silently un-fetching 4
    Bavarian boards / 2131 beds every night and re-attributing the real barmherzige.net board's
    postings to them instead (confirmed live 2026-09-18). A clinic's own non-empty careers_url wins
    unless it is itself recognisably part of the group ("own_ok", falling back to "match" for groups
    like kbo whose member sites often sit on a kbo-branded satellite domain that merely redirects
    into the shared board rather than serving its own content -- verified live 2026-09-18 that every
    such domain still matches the broad "kbo-|kbo\\.de" pattern, so keeping "match" as the kbo
    default changes nothing for it)."""
    name = (c.get("name") or "") + " " + (c.get("careers_url") or "")
    cu = c.get("careers_url") or ""
    for g in GROUP_PORTALS:
        if not re.search(g["match"], name, re.I):
            continue
        if cu and not re.search(g.get("own_ok", g["match"]), cu, re.I):
            continue
        return g
    return None


def _group_list_url(c, g):
    """The listing URL crawl_group_portal will actually page through for this clinic: its own
    pre-filtered querystring on the shared board when the registry careers_url already carries one
    (e.g. ?filter[company][]=<this clinic>), else the group's bare unfiltered list. Also the correct
    cache key for deduping a group fetch across sibling clinics -- callers must NOT dedupe by
    g["list"] alone, since two clinics can legitimately page two different filtered URLs on the same
    group host and must not skip each other's fetch."""
    cu = c.get("careers_url") or ""
    return cu if g["host"] in cu else g["list"]


def crawl_group_portal(c, g, session=None, towns=None):
    """Page the group board, then read each job's JSON-LD. Shared across every site of the group.

    Some clinics carry their own pre-filtered querystring on the shared board (e.g.
    ?filter[company][]=<this clinic>) in their registry careers_url -- start from that page rather
    than the group's bare listing URL, so a per-clinic filter that the tenant's own server already
    honours isn't silently dropped in favour of the whole group's unfiltered job list.

    `towns` (optional): validates a title_city_rx capture and an Einsatzort/PLZ-Ort text extraction
    against the registry town list (pflege_jobs.verify._placeable) before trusting either as the
    posting's own city -- without it both are accepted unchecked, same as parse_job_page elsewhere.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    base_list = _group_list_url(c, g)
    parts = urlsplit(base_list)
    base_qs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != g["page_param"]]
    urls, seen = [], set()
    for i in itertools.count(1):  # only the board's own end signal stops this: a bad response or a page with no fresh job link
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
        time.sleep(0.2)
    out = []
    for u in urls:
        r = get(u, session=session)
        if not r or not r.ok:
            continue
        j = parse_job_page(r.text, r.url, c["name"])
        if j and j.get("title"):
            from pflege_jobs.verify import _EINSATZORT, _PLZ_ORT, _clean_city, _placeable
            site_city = site_plz = None
            site_block_rx = g.get("site_block_rx")
            if site_block_rx:
                mb = site_block_rx.search(r.text)
                if mb:
                    # The block states the site of work, so its name is the posting's real employer
                    # site -- more specific than the group name the JSON-LD hiringOrganization
                    # carries on every posting of the board, and the only field that separates two
                    # registry sister sites sharing a town.
                    site_name = _txt(mb.group(1), 300)
                    if site_name:
                        j["org"], j["org_source"] = site_name, None
                    pm = _PLZ_ORT.search(_txt(mb.group(2)) or "")
                    if pm:
                        cand = _clean_city(pm.group(2))
                        if cand and _placeable(cand, pm.group(1), towns):
                            site_city, site_plz = cand, pm.group(1)
            title_city_rx = g.get("title_city_rx")
            if not site_city and title_city_rx:
                m = title_city_rx.search(j["title"].strip())
                if m:
                    cand = _clean_city(m.group(1))
                    if cand and _placeable(cand, None, towns):
                        site_city = cand
            if g.get("hq_location_untrusted"):
                # The group's own JSON-LD jobLocation is the group HQ, never the real work site
                # (see GROUP_PORTALS' hq_location_untrusted comment) -- it must not survive into
                # the row at all. The site block's own address wins, then a title-named site;
                # failing both, read the page's own Einsatzort/PLZ-Ort text (the same extraction
                # pflege_jobs.verify uses); failing that, leave city/plz/region None -- an honest
                # "unknown" beats the wrong HQ.
                if site_city:
                    j["loc"] = [{"city": site_city, "plz": site_plz, "region": None}]
                else:
                    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
                    city = plz = None
                    em = _EINSATZORT.search(text)
                    if em:
                        cand = _clean_city(em.group(1))
                        if cand and _placeable(cand, None, towns):
                            city = cand
                    if not city:
                        pm = _PLZ_ORT.search(text)
                        if pm:
                            cand = _clean_city(pm.group(2))
                            if _placeable(cand, pm.group(1), towns):
                                city, plz = cand, pm.group(1)
                    j["loc"] = [{"city": city, "plz": plz, "region": None}]
            elif site_city:
                j["loc"] = [{"city": site_city, "plz": site_plz or j["loc"][0].get("plz"),
                             "region": j["loc"][0].get("region")}]
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
    "asklepios": crawl_asklepios,
    "erecruiter": crawl_erecruiter,
    "concludis_widget": crawl_concludis_widget,
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
                # one fetch per group, reused by every member site (kbo: 9 clinics, 1 board) --
                # keyed by the URL crawl_group_portal will actually page, not the bare group list,
                # so a clinic with its own pre-filtered querystring is never skipped as "already
                # fetched" just because another clinic's unfiltered fetch ran first.
                key = _group_list_url(c, g)
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
                r["payload"]["loc"] = [{"city": c["town"], "plz": None, "region": None}]
        n = save(rows, "vendor_" + c["ats_type"])
        total += n
        print("  %-44s %-16s jobs %3d" % (c["name"][:44], c["ats_type"], n))
    print("total rows saved: %d -> %s" % (total, OUT))


if __name__ == "__main__":
    main()
