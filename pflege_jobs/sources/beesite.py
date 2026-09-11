"""beesite (muz global-jobboard-client): server-rendered career portal whose listing is filled by a
GJB (global-jobboard) search API on a per-tenant *.app.beesite.de host, injected into the page by
/script/render.js -- which itself answers 0 bytes to a plain GET (probed 2026-09-10 on
jobs.bezirkskliniken-mfr.de: same empty result with and without a warmed session cookie). No vendor
label exists in the registry for this engine (census left these boards "self_hosted") --
crawlers.vendor_adapters.crawl_wp_jobs probes the fingerprint below on every self_hosted/wp_jobs
board and delegates here; nothing in the registry changes (TASK-40 AC#3).

  fingerprint  BEESITE_FRONTEND cookie or "global-jobboard-client" in the page -- both already sit on
               the response crawl_wp_jobs fetched anyway, so the probe costs no extra request.
  job ids      <host>/sitemap.xml lists every current posting as index.php?ac=jobad&id=<n>, plain
               HTTP, no auth -- matched the GJB search API's own SearchResultCountAll exactly
               (40/40 on bezirkskliniken-mfr, probed 2026-09-10). The sitemap IS the full listing:
               no pagination, no ceiling -- its own length is the only stop.
  read path    the board's own client also calls index.php?ac=search_result before the JS-injected
               GJB search (the search form sits on that page) -- fetched here too so the adapter's
               calls cover it, even though job discovery itself comes from the sitemap.
  detail       index.php?ac=jobad&id=<n> carries a full schema.org JobPosting JSON-LD block
               (description, city, datePosted, employmentType, validThrough); on this tenant its own
               `title` field is polluted with a trailing org-boilerplate paragraph (confirmed live,
               id=971: "... - &lt;p&gt;&lt;br /&gt;...Mit zehn Kliniken...") -- the page's own <h1>
               is the clean title, used instead.
"""
import html as _html
import json
import re
import time
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"}
SITEMAP_JOB_ID = re.compile(r"ac=jobad&(?:amp;)?id=(\d+)")
LD_JSON = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)


def _get(u, session=None):
    # single tenant host for every call this adapter makes -- 0.5s politeness spacing (project rule).
    time.sleep(0.5)
    try:
        return (session or requests).get(u, headers=H, timeout=30, allow_redirects=True)
    except Exception:
        return None


def _txt(s, limit=20000):
    # unescape BEFORE stripping tags, not after: this source's JSON-LD double-encodes markup inside
    # its description string (literally "&lt;ul&gt;...", not "<ul>...") -- confirmed live, id=332 --
    # so a strip-then-unescape order (the shape every other vendor's html needs) would leave the
    # entity-decoded tags behind unstripped.
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _html.unescape(s or ""))).strip()[:limit] or None


def is_beesite(resp):
    """Cheap fingerprint on an already-fetched careers-page response -- no extra request.
    getattr, not resp.cookies: crawl_wp_jobs's own test suite fetches through a plain mock response
    with no .cookies attribute at all -- the html check alone is a sufficient fingerprint there."""
    if not resp:
        return False
    if "BEESITE_FRONTEND" in getattr(resp, "cookies", ()):
        return True
    return "global-jobboard-client" in (resp.text or "")


def _detail(html, url, org):
    """schema.org JobPosting JSON-LD on an ac=jobad page, title taken from <h1> (see module
    docstring on this tenant's polluted JSON-LD title field)."""
    for m in LD_JSON.finditer(html or ""):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for n in nodes:
            if not isinstance(n, dict) or "JobPosting" not in str(n.get("@type") or ""):
                continue
            h1 = H1.search(html)
            title = _txt(h1.group(1), 300) if h1 else (_txt(n.get("title"), 300) or "").split("&lt;")[0].strip()
            locs = n.get("jobLocation") or []
            locs = [locs] if isinstance(locs, dict) else locs
            addr = (locs[0] if locs else {}).get("address") or {}
            ho = n.get("hiringOrganization") or {}
            return {"title": title or None,
                    "org": (ho.get("name") if isinstance(ho, dict) else ho) or org,
                    "loc": [{"city": addr.get("addressLocality"), "plz": addr.get("postalCode"),
                             "region": addr.get("addressRegion")}],
                    "url": url, "page": url, "datePosted": (n.get("datePosted") or "")[:10] or None,
                    "employmentType": n.get("employmentType"), "description": _txt(n.get("description"))}
    return None


def crawl_beesite(c, session=None):
    cu = (c.get("careers_url") or "").strip()
    if not cu:
        return []
    p = urlparse(cu)
    base = "%s://%s" % (p.scheme, p.netloc)

    # Matches the board's own client read path (see module docstring) -- job ids still come from
    # the sitemap below; this call exists so the adapter's read-path coverage includes it too.
    _get(base + "/index.php?ac=search_result", session=session)

    sm = _get(base + "/sitemap.xml", session=session)
    ids = sorted({m.group(1) for m in SITEMAP_JOB_ID.finditer(sm.text)}, key=int) if sm and sm.ok else []

    out = []
    for jid in ids:
        u = "%s/index.php?ac=jobad&id=%s" % (base, jid)
        r = _get(u, session=session)
        if not r or not r.ok:
            continue
        j = _detail(r.text, r.url, c["name"])
        if not j or not j.get("title"):
            continue
        if not j["loc"][0]["city"] and c.get("town"):
            j["loc"] = [{"city": c["town"], "plz": None, "region": "BAYERN"}]
        out.append({"kind": "jobposting", "source_host": p.netloc, "source_url": j["url"],
                    "payload": j, "collector": "vendor-beesite-v1", "client_id": "vendor-adapters-beesite"})
    return out
