"""Shared contract for the adapter-completeness suite.

An adapter is *complete* for a board when it uses every read path the board's own client uses and
returns everything that client can show. The board is the oracle — never our own parser:

  read paths     API boards: the list/detail endpoints the page and its JS bundles call.
                 Server-rendered boards: the pagination + detail links reachable from the listing.
                 The adapter's calls must COVER these (superset on read paths, not set equality —
                 the client also calls apply/analytics/auth endpoints the adapter must not touch).
  declared total the board's own count (API totalFound / numberOfItems / page.total, or "N Stellen"
                 on the page) must equal rows returned after walking every page.
  fields         every source field that maps to our schema is populated (description, city, dates).
  public url     the stored url is a browsable page, not an API self-link.
  round trip     a sample of stored rows resolves live with the same title.

Nothing here filters: completeness is about coverage, not about which postings we keep.
tests/test_adapter_completeness.py parameterises these over the live registry; every page fetched
here is also written to the snapshot folder, so the mirror grows as a side effect of testing.
"""
import hashlib
import html
import json
import os
import re
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

PROXY = "https://supabase.int.exe.xyz/rest/v1"
HDR = {"Accept-Profile": "pflege_jobs"}
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
SNAPSHOT_ROOT = Path(os.environ.get("SNAPSHOT_ROOT", "crawl_snapshots"))

# Read-path families a board's client may use. Name -> regex over page+script text. The adapter for
# that board must call the same family (checked by endpoint shape, see `endpoint_key`).
API_HINTS = (
    ("smartrecruiters", r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)/postings"),
    ("dvinci",          r"https?://[^\s\"'`]+/jobPublication/list\.json"),
    ("personio",        r"https?://[a-z0-9-]+\.jobs\.personio\.(?:de|com)/xml"),
    ("softgarden",      r"https?://[^\s\"'`]+/jobs\.feed\.json"),
    ("bite",            r"https?://jobs\.b-ite\.com/api/[^\s\"'`]+|/_json\.jobs\.php"),
    ("umantis",         r"https?://recruitingapp-\d+\.[a-z.]+/Jobs/(?:All|\d+)"),
    ("beesite",         r"index\.php\?ac=(?:search_result|jobad)"),
    ("hr4you",          r"https?://[a-z0-9-]+\.hr4you\.org/job/view/\d+"),
    ("mein-check-in",   r"https?://(?:www\.)?mein-check-in\.de/[a-z0-9-]+/(?:overview|position)"),
    # https?:// anchor required (unlike a bare "/stellenangebote.html?"): a hospital's own CMS can name
    # a page that too (seen live: klinikum-kulmbach.de/beruf-ausbildung/stellenangebote.html?print=1),
    # false-matching as a rexx read path with no host to tell it apart from the real tenant board.
    ("rexx",            r"https?://[^\s\"'`]+/stellenangebote\.html\?|https?://[^\s\"'`]+-de-j\d+\.html"),
    # (?:https?://[^\s"'`]*?)? optional scheme+host prefix -- without it, a share-link that embeds
    # the full "https://tenant.helixjobs.com/unit/joblist" URL in page JS matches starting mid-string
    # (regex has no scheme anchor), and the later urljoin(page_url, match) then treats that schemeless
    # match as a path relative to the current page, corrupting it into a nonsense nested URL.
    ("helix",           r"(?:https?://[^\s\"'`]*?)?helixjobs\.com/[^\s\"'`]*/(?:joblist|jobad)"),
)

# Client-side fetches: fetch()/axios/XHR targets, `url:` literals, data-* endpoint attributes.
# (?<!window) on .open -- window.open(url, target) is a browser-tab navigation a click handler
# fires, never an XHR data read; only XMLHttpRequest-style receivers should count as a fetch.
FETCH_RX = re.compile(
    r"""(?:fetch|axios(?:\.get|\.post)?|(?<!window)\.open)\s*\(\s*(?:["'`]\w+["'`]\s*,\s*)?["'`]([^"'`]{4,300})"""
    r"""|\b(?:url|endpoint|api|feed)\s*[:=]\s*["'`]([^"'`]{4,300})["'`]"""
    r"""|data-[a-z-]*(?:api|endpoint|url|feed|listing)[a-z-]*\s*=\s*["']([^"']+)""", re.I)
SCRIPT_SRC_RX = re.compile(r"<script[^>]+src=[\"']([^\"']+)", re.I)
COUNT_RX = re.compile(r"(?<![%\w-])(\d{1,4})\s*(?:offene\s+)?(?:Stellen(?:angebote|anzeigen)?|Jobs|Vakanzen|Position(?:en)?(?!-)|Treffer|Ergebnisse)\b", re.I)
# Position(?!-) -- a Bootstrap-shaped "w-100 position-relative"/"col-lg-4 position-absolute" class
# pair reads as "100 Position"/"4 Position" without the trailing-hyphen guard: real German job-count
# text never continues straight into a CSS modifier like that.
# (?<![%\w-]) -- without it, a number ending a URL-encoded space ("...%20Jobs%2F...") or any other
# digit/word run immediately before the keyword false-matches as a declared total (real case: a
# WordPress post's URL-encoded og:title "...%20-%20Jobs%2FKarriere" read as "declares 20"). The
# hyphen guard: a WP custom-post-type slug literally named "stellenangebote" prints its own numeric
# post id right before its own class name ("post-942 stellenangebote", "loop-item-3237 post-3237
# stellenangebote") -- that id, not a count, was winning as the page's "declared total" on two real
# boards (veramed.de, krankenhaus-st-camillus.de).
# Analytics/consent bundles are never a board's read path.
NOISE_HOSTS = re.compile(r"googletagmanager|google-analytics|gstatic|matomo|piwik|usercentrics|cookiebot|consentmanager|hotjar|facebook|doubleclick|youtube", re.I)
# A same-host "job newsletter" subscribe widget (concludis-platform tenants: form data-url/
# data-sendmail-url pointing at .../jobletter and .../jobletter/sendemail) is a write-only email-
# signup action, never a posting read -- but it contains "job" so the api_urls keyword filter below
# would otherwise treat it as one. NOISE_HOSTS only matches by host, not path, so it misses this.
NOISE_PATHS = re.compile(r"/jobletter(?:/|\?|$)", re.I)
STATIC_EXT = (".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".pdf")
PAGINATION_RX = re.compile(r"(?:[?&](?:page|seite|start|offset|p)=\d+|tx_solr(?:%5B|\[)page(?:%5D|\])=\d+|[?&]c_page=\d+|/Jobs/\d+|/page/\d+)", re.I)
SR_ONLY_RX = re.compile(r'<span[^>]*\bsr-only\b[^>]*>.*?</span>', re.I | re.S)


def _get(url, timeout=25, session=None):
    import requests
    try:
        return (session or requests).get(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"},
                                         timeout=timeout, allow_redirects=True)
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# registry / boards
# ---------------------------------------------------------------------------------------------
def live_clinics():
    """Registry rows straight from the read proxy; the CSV in data/registry is stale."""
    out, off = [], 0
    while True:
        q = (f"{PROXY}/clinics?select=clinic_id,name,town,careers_url,website,ats_type,beds"
             f"&limit=1000&offset={off}")
        b = json.load(urllib.request.urlopen(urllib.request.Request(q, headers=HDR), timeout=60))
        out += b
        off += 1000
        if len(b) < 1000:
            break
    return out


def boards(clinics=None):
    """Board = grouped careers_url, exactly as crawlers.routing.plan() groups it."""
    from crawlers.routing import plan
    b, _ = plan([dict(c) for c in (clinics or live_clinics())])
    return b


# ---------------------------------------------------------------------------------------------
# snapshot: every page a test fetches lands on disk (R1 -- the mirror grows as a side effect)
# ---------------------------------------------------------------------------------------------
def snapshot_dir(board_url):
    h = urlparse(board_url).netloc.lower().removeprefix("www.")
    d = SNAPSHOT_ROOT / h / date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(board_url, url, body, status=200, content_type="text/html"):
    """Write one fetched page under the board's dated folder and append its manifest line."""
    d = snapshot_dir(board_url)
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + (".json" if "json" in content_type else ".html")
    (d / name).write_text(body, encoding="utf-8", errors="replace")
    with open(d / "manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"url": url, "file": name, "status": status, "bytes": len(body),
                            "content_type": content_type, "sha256": hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()},
                           ensure_ascii=False) + "\n")
    return d / name


# ---------------------------------------------------------------------------------------------
# the oracle: what the board's own client reads
# ---------------------------------------------------------------------------------------------
def endpoint_key(url):
    """Shape of an endpoint, ignoring values: host + path with digits/hashes collapsed + sorted
    query param NAMES. Two calls to the same endpoint with different page numbers share a key."""
    p = urlparse(url)
    path = re.sub(r"[0-9a-f]{8,}|\d+", "N", p.path)
    names = sorted({k for k, _ in parse_qsl(p.query, keep_blank_values=True)})
    return urlunparse((p.scheme or "https", p.netloc.lower().removeprefix("www."), path, "", "&".join(names), ""))


def client_read_paths(careers_url, session=None, max_scripts=6, snapshot=True):
    """Every read path the board's own client uses, from the page and the scripts it loads --
    including cross-origin vendor bundles (the smartrecruiters tenant lived only inside
    static.smartrecruiters.com/job-widget/..., never in the page). Analytics/consent bundles skipped.

    Returns {vendors, urls, api_urls, pagination, detail_links, html, final_url, error}.
    `api_urls` are read paths that look like posting reads; the adapter must cover their keys."""
    r = _get(careers_url, session=session)
    if not r or not r.ok:
        return {"error": f"HTTP {getattr(r, 'status_code', 'none')}", "vendors": set(), "urls": set(),
                "api_urls": set(), "pagination": set(), "detail_links": set(), "html": "", "final_url": careers_url}
    if snapshot:
        save(careers_url, r.url, r.text, r.status_code)
    bodies = [r.text]
    for src in SCRIPT_SRC_RX.findall(r.text)[:max_scripts]:
        u = urljoin(r.url, src)
        if NOISE_HOSTS.search(u):
            continue
        s = _get(u, session=session)
        if s and s.ok and len(s.text) < 3_000_000:
            bodies.append(s.text)
            if snapshot:
                save(careers_url, u, s.text, s.status_code, "application/javascript")
    blob = "\n".join(bodies)
    vendors = {name for name, rx in API_HINTS if re.search(rx, blob, re.I)}
    urls = set()
    for m in FETCH_RX.finditer(blob):
        u = html.unescape(next((g for g in m.groups() if g), ""))
        if (u.startswith(("http", "/")) and not u.lower().endswith(STATIC_EXT)
                and not NOISE_HOSTS.search(u) and not NOISE_PATHS.search(u)):
            urls.add(urljoin(r.url, u))
    for _, rx in API_HINTS:
        for m in re.finditer(rx, blob, re.I):
            urls.add(urljoin(r.url, html.unescape(m.group(0))))
    api_urls = {u for u in urls if re.search(r"job|stellen|posting|vacan|position|feed|list\.json|/xml\b", u, re.I)}
    from crawlers.vendor_adapters import JOB_PATH
    # html.unescape -- a data-url attribute's own query string is HTML-escaped in the source
    # ("...&amp;tx_..."); comparing that raw text's endpoint_key() against the adapter's own
    # (already-unescaped, see crawlers.vendor_adapters._widget_endpoint_job_links) call mangled the
    # query param names into one giant "&amp;"-joined literal, so a real call never matched (real
    # case: medbo.de's own cn_medbo_jobs AJAX endpoint, called correctly, still read as "missing").
    hrefs = {urljoin(r.url, html.unescape(h)) for h in re.findall(r'href=["\']([^"\'#]+)', r.text)}
    pagination = {h for h in hrefs if PAGINATION_RX.search(h)}
    detail_links = {h for h in hrefs if JOB_PATH.search(urlparse(h).path)}
    return {"error": None, "vendors": vendors, "urls": urls, "api_urls": api_urls, "pagination": pagination,
            "detail_links": detail_links, "html": r.text, "final_url": r.url}


def declared_total(html, api_json=None):
    """The board's own claim about how many postings it has -- the one completeness oracle that
    does not depend on our parsing. API totals win over page text."""
    if isinstance(api_json, dict):
        for k in ("totalFound", "numberOfItems", "total", "totalCount", "count"):
            v = api_json.get(k)
            if isinstance(v, int):
                return v
        pg = api_json.get("page") or {}
        if isinstance(pg, dict) and isinstance(pg.get("total"), int):
            return pg["total"]
    text = html or ""
    hits = [(m.start(), int(m.group(1))) for m in COUNT_RX.finditer(text)]
    if not hits:
        return None
    sr_spans = [(m.start(), m.end()) for m in SR_ONLY_RX.finditer(text)]
    # Every hit sitting inside a screen-reader-only span (e.g. Personio's per-group job-count
    # badges) is a partition subtotal, never a page-level total -- summing the distinct groups
    # beats guessing the largest single group is the whole board's count.
    if sr_spans and all(any(s <= pos < e for s, e in sr_spans) for pos, _ in hits):
        return sum(v for _, v in hits)
    return max(v for _, v in hits)


def missing_read_paths(adapter_urls, client):
    """Read paths the client uses that the adapter never called (by endpoint shape).
    Empty set == the adapter covers the board's read surface."""
    have = {endpoint_key(u) for u in adapter_urls}
    return {u for u in client["api_urls"] if endpoint_key(u) not in have}


def is_public_url(u):
    """A stored posting url must open in a browser as a posting -- never an API self-link."""
    return bool(u) and not re.search(r"api\.smartrecruiters\.com|/jobPublication/|\.json(\?|$)|/xml(\?|$)|/api/v\d", u, re.I)


# ---------------------------------------------------------------------------------------------
# spies: what the adapter actually called
# ---------------------------------------------------------------------------------------------
class RecordCalls:
    """Records every URL an adapter run actually COVERED, whichever client it uses:
    crawlers.vendor_adapters.get (vendor family) and requests.Session.request (seeded family).
    "Covered" means the request came back ok -- a call recorded even when it 404s/errors would let
    an adapter with only one API-shaped read path (nothing downstream depends on its result) pass
    read-path coverage by merely attempting a now-broken endpoint, never actually using it."""

    def __enter__(self):
        import requests
        from crawlers import vendor_adapters as VA
        self.urls = []
        self._va_get, self._sess_req = VA.get, requests.Session.request

        def spy_get(u, timeout=30, session=None):
            resp = self._va_get(u, timeout=timeout, session=session)
            if resp is not None and getattr(resp, "ok", True):
                self.urls.append(u)
            return resp

        rec = self

        def spy_req(self_, method, url, *a, **k):
            resp = rec._sess_req(self_, method, url, *a, **k)
            if resp is not None and getattr(resp, "ok", True):
                rec.urls.append(url)
            return resp

        VA.get = spy_get
        requests.Session.request = spy_req
        return self

    def __exit__(self, *a):
        import requests
        from crawlers import vendor_adapters as VA
        VA.get, requests.Session.request = self._va_get, self._sess_req
        return False


# ---------------------------------------------------------------------------------------------
# mutations: break an adapter on purpose so a test proves it would notice
# ---------------------------------------------------------------------------------------------
MUTATIONS = {
    "cap_first_page": "stop after the first list page -- the count check must go red",
    "drop_description": "return rows with description=None -- the field check must go red",
    "api_self_link": "store the API self-link as url -- the public-url check must go red",
    "skip_detail": "never fetch detail endpoints -- the read-path check must go red",
}


def offline():
    return os.environ.get("PFLEGE_TESTS_OFFLINE") == "1"
