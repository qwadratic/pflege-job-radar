"""softgarden career-site adapter (source employer_ats). Recipe:
  detect: page links/scripts to <tenant>.career.softgarden.de / *.softgarden.io / jobdb.softgarden.de/<slug>,
          or a custom domain whose HTML contains 'softgarden' + /job/<id>/ links
  feed:   https://<host>/jobs.feed.json -- a schema.org DataFeed of JobPosting items, no BFS needed.
  fallback (feed missing/404, e.g. a non-softgarden-hosted board that slipped past detection):
          https://<host>/de/vacancies (server-rendered) + https://<host>/sitemap.xml (job URLs), walked by
          career_crawl.Crawler's normal BFS + JSON-LD-per-page path.
"""
import re, requests
from urllib.parse import urlparse
from .career_crawl import UA

SG_HOST = re.compile(r"https?://([a-z0-9\-\.]+\.(?:career\.softgarden\.de|softgarden\.io)|jobdb\.softgarden\.de/[a-z0-9\-]+)", re.I)


def find_host(career_url, session=None):
    """-> (host_base_url, evidence) or (None, None)."""
    s = session or requests.Session()
    try:
        r = s.get(career_url, headers={"User-Agent": UA}, timeout=30)
    except requests.RequestException:
        return None, None
    html = r.text
    m = SG_HOST.search(html)
    if m:
        return "https://" + m.group(1).rstrip("/"), "link:" + m.group(1)
    if re.search(r"softgarden", html, re.I) and re.search(r"/job/\d+/", html):
        p = urlparse(r.url); return f"{p.scheme}://{p.netloc}", "custom-domain"
    return None, None


def seed_for(facility, kez, town, session=None):
    host, ev = find_host(facility["career"], session)
    if not host:
        return None
    own = urlparse(facility["career"])
    own_host = f"{own.scheme}://{own.netloc}" if own.scheme and own.netloc else None
    # Many softgarden customers CNAME their own domain straight onto the feed endpoint: /jobs.feed.json
    # answers on the custom domain (jobs.pkd.de, karriere.klinikum-bayreuth.de) even though /de/vacancies
    # 404s there and the page's own links point at a *.softgarden.io / *.career.softgarden.de host that may
    # in turn be the wrong branch for a multi-site operator. Try the facility's own domain first.
    feed_hosts = ([own_host] if own_host and own_host != host else []) + [host]
    return {"name": facility["name"], "kez": kez, "town": town, "career": host + "/de/vacancies", "extra_seeds": [host + "/vacancies", host],
            "sitemaps": [host + "/sitemap.xml"], "hosts": [urlparse(host).netloc], "bavaria_only_operator": True, "ats": "softgarden", "evidence": ev,
            "host": host, "feed_hosts": feed_hosts}


def fetch_feed(hosts, session=None, timeout=30):
    """Try https://<host>/jobs.feed.json for each host in order -> (items, host_used) for the first host
    that answers with a non-empty schema.org DataFeed (dataFeedElement[].item), or (None, None) if none do
    (caller should fall back to BFS).

    Section-first check (2026-09): this feed's JobPosting items never carry a category/department field
    (schema.org's industry/occupationalCategory/employmentUnit are all absent) on any live tenant checked
    -- verified again on jobs.pkd.de and a starnberger-kliniken.de tenant (110 items, identical key set:
    @type/title/url/datePosted/identifier/description/employmentType/hiringOrganization/jobLocation).
    A real per-tenant department taxonomy does exist one layer up in some tenants' website widget config
    (filterPresets.category, e.g. klinikum-bayreuth.de's "Pflegedienst und Funktionsdienst"), but it is
    rendered/applied client-side against an internal widget API this module doesn't call -- not reachable
    from the feed fetched here. So there is nothing to mine first; left unchanged, full feed + the
    existing Bavaria-filter/dedupe still does the work."""
    s = session or requests.Session()
    for host in hosts:
        try:
            r = s.get(host.rstrip("/") + "/jobs.feed.json", headers={"User-Agent": UA}, timeout=timeout)
            if r.status_code != 200:
                continue
            data = r.json()
        except (requests.RequestException, ValueError):
            continue
        elements = data.get("dataFeedElement") or []
        items = [e["item"] for e in elements if isinstance(e, dict) and isinstance(e.get("item"), dict)]
        if items:
            return items, host
    return None, None
