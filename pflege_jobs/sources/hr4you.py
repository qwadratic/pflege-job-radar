"""hr4you: one tenant subdomain per employer site (<tenant>.hr4you.org), plain HTTP throughout -- no
Playwright needed. Probed 2026-09-10 on ATOS (www.atos-karriere.de): the operator's own careers page
and its own script bundles name no hr4you host at all, but a one-hop follow to a location-filter
subpage the page itself links to does (21 tenants, 67 postings on /standorte/) -- the same "career
page may only link to the joblist" shape as crawlers.vendor_adapters.crawl_helix. No vendor label
exists in the registry for this engine either (census left ATOS "self_hosted"); crawl_wp_jobs falls
back here only once its own generic discovery finds nothing, so nothing in the registry changes
(TASK-40 AC#3).

  tenant discovery  the careers page (or, failing that, the first same-site link whose path names a
                     location/jobs/careers section) already links directly to <tenant>.hr4you.org/
                     job/view/<id>/<slug> postings -- the tenant hostnames are read straight off
                     those links, no separate directory API.
  job ids           every tenant answers a plain, unauthenticated /sitemap.xml (its own robots.txt
                     explicitly Allows it) listing every current job/view/<id> url -- summed across
                     ATOS's 21 tenants this matched the /standorte/ page's own link count exactly
                     (67/67, probed 2026-09-10). No pagination, no ceiling: each tenant's sitemap is
                     already its complete listing.
  detail            job/view/<id> carries a full schema.org JobPosting JSON-LD block (description,
                     city, datePosted, employmentType) -- no separate list API needed.
"""
import json
import re
import time
from urllib.parse import urldefrag, urljoin, urlparse

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"}
TENANT_JOB = re.compile(r"https?://([a-z0-9-]+\.hr4you\.org)/job/view/\d+/[^\s\"'<>]*", re.I)
# Path-only, on purpose: a naive whole-URL match false-hits on any operator whose own domain happens
# to contain "karriere" (atos-karriere.de itself, matched on every single link on the page).
SUBPAGE_HINT = re.compile(r"standort|/jobs?/|karriere/|stellenangebot", re.I)
LD_JSON = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def _get(u, session=None):
    # sequential, one request at a time -- 0.5s politeness spacing (project rule), same on every
    # tenant host even though each one alone sees only a handful of calls.
    time.sleep(0.5)
    try:
        return (session or requests).get(u, headers=H, timeout=30, allow_redirects=True)
    except Exception:
        return None


def _txt(s, limit=20000):
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()[:limit] or None


def _tenant_hosts(html):
    return {m.group(1).lower() for m in TENANT_JOB.finditer(html or "")}


def _detail(html, url, org):
    for m in LD_JSON.finditer(html or ""):
        try:
            d = json.loads(m.group(1).strip())
        except Exception:
            continue
        if not isinstance(d, dict) or "JobPosting" not in str(d.get("@type") or ""):
            continue
        loc = d.get("jobLocation") or {}
        loc = (loc[0] if loc else {}) if isinstance(loc, list) else loc
        addr = (loc or {}).get("address") or {}
        ho = d.get("hiringOrganization") or {}
        return {"title": _txt(d.get("title"), 300),
                "org": (ho.get("name") if isinstance(ho, dict) else ho) or org,
                "loc": [{"city": addr.get("addressLocality"), "plz": addr.get("postalCode"),
                         "region": addr.get("addressRegion")}],
                "url": url, "page": url, "datePosted": (d.get("datePosted") or "")[:10] or None,
                "employmentType": d.get("employmentType"), "description": _txt(d.get("description"))}
    return None


def _discover_tenants(cu, session=None):
    """Tenant hostnames linked from the careers page itself, or (see module docstring) from the
    first same-site subpage its own links point to that names a location/jobs/careers section."""
    r = _get(cu, session=session)
    if not r or not r.ok:
        return set()
    tenants = _tenant_hosts(r.text)
    if tenants:
        return tenants
    for h in dict.fromkeys(re.findall(r'href="([^"#]+)"', r.text)):
        u = urldefrag(urljoin(r.url, h))[0]
        if u == r.url or urlparse(u).netloc != urlparse(r.url).netloc:
            continue
        if not SUBPAGE_HINT.search(urlparse(u).path):
            continue
        r2 = _get(u, session=session)
        if r2 and r2.ok:
            tenants = _tenant_hosts(r2.text)
            if tenants:
                return tenants
    return set()


def crawl_hr4you(c, session=None):
    cu = (c.get("careers_url") or "").strip()
    if not cu:
        return []
    out = []
    for host in sorted(_discover_tenants(cu, session=session)):
        sm = _get("https://%s/sitemap.xml" % host, session=session)
        urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sm.text) if sm and sm.ok else []
        for u in urls:
            r = _get(u, session=session)
            if not r or not r.ok:
                continue
            j = _detail(r.text, r.url, c["name"])
            if not j or not j.get("title"):
                continue
            if not j["loc"][0]["city"] and c.get("town"):
                j["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
            out.append({"kind": "jobposting", "source_host": host, "source_url": j["url"],
                        "payload": j, "collector": "vendor-hr4you-v1", "client_id": "vendor-adapters-hr4you"})
    return out
