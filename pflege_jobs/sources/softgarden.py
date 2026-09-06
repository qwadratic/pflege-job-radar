"""softgarden career-site adapter (source employer_ats). Recipe:
  detect: page links/scripts to *.softgarden.io / jobdb.softgarden.de, or a custom domain whose HTML contains 'softgarden' + /job/<id>/ links
  list:   https://<host>/de/vacancies (server-rendered) + https://<host>/sitemap.xml (job URLs)
  detail: JSON-LD JobPosting on every job page (softgarden emits it) -> career_crawl.Crawler does the rest.
"""
import re, requests
from urllib.parse import urlparse
from .career_crawl import UA

SG_HOST = re.compile(r"https?://([a-z0-9\-\.]+\.softgarden\.io|jobdb\.softgarden\.de/[a-z0-9\-]+)", re.I)


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
    return {"name": facility["name"], "kez": kez, "town": town, "career": host + "/de/vacancies", "extra_seeds": [host + "/vacancies", host],
            "sitemaps": [host + "/sitemap.xml"], "hosts": [urlparse(host).netloc], "bavaria_only_operator": True, "ats": "softgarden", "evidence": ev}
