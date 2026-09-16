"""Web-liveness verification: is each posting still reachable on the public web?

Generic for every source (career sites, Firecrawl agent): GET the job URL with a browser UA, follow
  redirects -> 404/410 gone; 200 whose body still
  contains the job title (first 3 significant words) -> live; 200 without the title (redirected to a job list,
  "Stelle nicht mehr verfügbar" page) -> gone_soft (reported as gone with note); 403/429/5xx/timeouts -> blocked/error.
Results are pushed to pflege_jobs.postings via the ingest function (`verify` op). Only 'gone' expires a posting.

Escalation (2026-09-16, Ivan: a status set without a real request does not count, and the crawler must
not give up where a browser would get through): plain HTTP -> Playwright -> Firecrawl. Plain HTTP cannot
decide three shapes at all, so those escalate rather than being recorded as a verdict:
  * a job id that lives in the URL FRAGMENT (".../jobs#/detail/123") -- the server never sees it, so HTTP
    fetches the bare list page and would either fake a 'live' off the list or call it gone;
  * a JS-rendered detail page (the 'error: 200 but title not found' bucket -- 59 rows today);
  * a soft anti-bot wall (403/429 to a datacenter IP that a real browser passes).
Only a verdict that came from a request that could actually see the posting is written back; `method`
records which rung produced it, so "verified" is never an assertion.
"""
import json as _json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from .classify import norm_text

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
GONE_MARKERS = re.compile(r"nicht mehr verfügbar|nicht mehr online|nicht gefunden|stelle wurde bereits besetzt|"
                          r"job is no longer|no longer available|position has been filled|page not found|404", re.I)
# A bot wall answers 200 with its own refusal page. Without this it lands in the 'error: 200 but title
# not found' bucket and reads like a broken adapter, when the honest verdict is "we were refused"
# (confirmed live 2026-09-16: every www.helios-gesundheit.de posting -- 556 rows, the biggest single
# host in the table -- is an Akamai "Access Denied" to both plain HTTP and headless Chromium).
WALL_MARKERS = re.compile(r"access denied|you don't have permission to access|errors\.edgesuite\.net|"
                          r"just a moment\.\.\.|cf-browser-verification|attention required!|请稍候|"
                          r"incapsula incident id|radware|bot detection|unusual traffic", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _title_tokens(title):
    toks = [t for t in re.findall(r"[a-zäöüß]{4,}", norm_text(title or "")) if t not in ("pflegefachkraft", "gesundheits", "krankenpfleger")]
    return toks[:3]


def decide(status_code, body, title, exc_name=None):
    """Pure live/gone decision (unit-tested, used by the Settings "try it" box).

    -> (verify_status, http, note). exc_name = transport error class name when no response arrived.
    """
    if exc_name:
        return "error", None, exc_name
    if status_code in (404, 410):
        return "gone", status_code, None
    if status_code in (401, 403, 429):
        return "blocked", status_code, None
    if status_code >= 500 or status_code != 200:
        return "error", status_code, None
    body = norm_text((body or "")[:400000])
    if WALL_MARKERS.search(body[:4000]):
        return "blocked", 200, "bot wall (200 with a refusal page)"
    toks = _title_tokens(title)
    hit = sum(1 for t in toks if t in body)
    if toks and hit == 0 and GONE_MARKERS.search(body):
        return "gone", 200, "200 but title missing + gone marker"
    if toks and hit == 0:
        return "error", 200, "200 but title not found (JS-rendered or list page)"
    return "live", 200, f"title tokens {hit}/{len(toks)}"


def verify_url(session, url, title):
    try:
        r = session.get(url, headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"}, timeout=40, allow_redirects=True)
    except requests.RequestException as e:
        return decide(None, None, title, exc_name=type(e).__name__)
    return decide(r.status_code, r.text, title)


# --- location off the page itself ---------------------------------------------------------------
# The city stored on a posting is whatever the crawler put there, and a crawler that found no
# per-posting location falls back to the seed clinic's own town -- which is how postings for
# Haldensleben/Oberhausen/Hameln ended up stored as "Neuburg/Donau" (TASK-59a). The only city that
# can be trusted is the one the posting's own page states, so read it back here while the page is
# already open.
_LD_BLOCK = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_PLZ_ORT = re.compile(r"\b(\d{5})\s+([A-ZÄÖÜ][\wäöüß.\-]+(?:\s+[A-ZÄÖÜa-zäöüß.\-]+){0,2})")
_EINSATZORT = re.compile(r"(?:Einsatzort|Arbeitsort|Standort|Dienstort)\s*[:\-–]?\s*([A-ZÄÖÜ][\wäöüß.\-]+(?:\s+[A-ZÄÖÜa-zäöüß.\-]+){0,2})")


def _walk_jsonld(node, out):
    """Collect every JobPosting jobLocation address in a JSON-LD document (dict, list or @graph)."""
    if isinstance(node, list):
        for x in node:
            _walk_jsonld(x, out)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        _walk_jsonld(node["@graph"], out)
    loc = node.get("jobLocation")
    if loc:
        for pl in (loc if isinstance(loc, list) else [loc]):
            addr = (pl or {}).get("address") if isinstance(pl, dict) else None
            if isinstance(addr, dict):
                out.append((addr.get("addressLocality"), addr.get("postalCode")))
    for v in node.values():
        if isinstance(v, (dict, list)):
            _walk_jsonld(v, out)


# A '12345 Ort' / 'Einsatzort: Ort' capture runs on into whatever follows it on the page ("Bad Aibling
# Jetzt bewerben"), so cut at the first token that cannot be part of a place name. Lowercase words are
# only kept when they are the connectors real German place names use ("Neuburg an der Donau").
_CITY_STOP = re.compile(r"^(jetzt|bewerben|bewerbung|stellen\w*|stelle|voll\w*|teil\w*|gmbh|ggmbh|ag|kg|ev|karriere|job\w*|"
                        r"wir|ihre|unsere|unser|mehr|zum|zur|ab|sofort|befristet|unbefristet|kontakt|impressum|standort\w*|"
                        r"datum|start\w*|arbeitgeber|klinik\w*|krankenhaus|zentrum|abteilung|tel\w*|fax|e|mail|"
                        r"einstieg\w*|berufs\w*|beschäftigung\w*|arbeitszeit|eintritt|bereich|fachbereich|position|"
                        r"referenz\w*|kennziffer|anstellung\w*|verguetung|vergütung|schicht|erfahren\w*)$", re.I)
_CITY_CONNECT = {"an", "der", "am", "im", "bei", "ob", "vor", "auf", "in", "a.d.", "i.d.", "a.", "i."}


def _clean_city(s):
    if not s:
        return None
    out = []
    for i, p in enumerate(str(s).replace("\xa0", " ").split()):
        w = p.strip(",.;:|–-")
        if not w:
            break
        if i and (_CITY_STOP.match(w) or (w[:1].islower() and w.lower() not in _CITY_CONNECT)):
            break
        out.append(w)
    city = " ".join(out) or None
    if city and city.lower() in _CITY_JUNK:
        return None
    return city


# Only these two sources SAY what the job's location is. A bare '12345 Ort' found anywhere on the page
# is just the first address on it -- regularly the operator's head office in the footer, a sister site
# in a group listing, or plain prose. Trusting it produced "Viechtach -> Ulm (89077)" and cities called
# 'Ich' and 'Klinik' (2026-09-16), so plz_ort is returned but is only ever CONFIRMING evidence.
TRUSTED_LOC = ("jsonld", "einsatzort")
_CITY_JUNK = {"ich", "wir", "sie", "die", "der", "das", "klinik", "kliniken", "klinikum", "krankenhaus",
              "unser", "unsere", "haus", "team", "stelle", "pflege", "bewerbung", "kontakt", "adresse"}


def extract_location(html):
    """(city, plz, source) straight off the posting page. JSON-LD first (a real JobPosting field),
    then an 'Einsatzort:' label, then a bare '12345 Ort' pair -- see TRUSTED_LOC for which of those a
    caller may act on. (None, None, None) when the page says nothing."""
    if not html:
        return None, None, None
    for m in _LD_BLOCK.finditer(html):
        try:
            doc = _json.loads(m.group(1).strip())
        except Exception:
            continue
        hits = []
        _walk_jsonld(doc, hits)
        for city, plz in hits:
            city = (city or "").strip() if isinstance(city, str) else None
            plz = str(plz).strip() if plz else None
            if city or plz:
                return _clean_city(city), plz or None, "jsonld"
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    m = _EINSATZORT.search(text)
    if m:
        c = _clean_city(m.group(1))
        if c:
            return c, None, "einsatzort"
    m = _PLZ_ORT.search(text)
    if m:
        return _clean_city(m.group(2)), m.group(1), "plz_ort"
    return None, None, None


# --- escalation rungs ---------------------------------------------------------------------------
# ".../jobs#/detail/123", ".../stellen#jobid=88": the id never reaches the server, so HTTP would judge
# the bare list page instead of the posting. A plain in-page anchor ("#content") carries no id and is
# left on the HTTP rung.
FRAGMENT_URL = re.compile(r"#/\w|#[\w\-]+=|#[\w\-=/]*\d")
IS_PDF = re.compile(r"\.pdf(?:[?#]|$)", re.I)


def render(url, wait_ms=4500, timeout_ms=30000):
    """Playwright rung. (status_code, html, final_url) -- status is 200 when the page rendered at all,
    None when the navigation itself failed. Reuses crawlers.portals' single shared browser.

    A wall answers instantly with a tiny refusal page, so that case returns as soon as it is
    recognised instead of sitting out the JS settle time -- across a full pass that is the difference
    between ~6s and ~1s on every one of the 556 Akamai-walled rows."""
    from crawlers.portals import fetch_page, dismiss_consent
    page = ctx = None
    try:
        page, ctx = fetch_page(url, wait_ms=600, timeout_ms=timeout_ms)
        early = page.content()
        if WALL_MARKERS.search(norm_text(early[:4000])):
            return 200, early, page.url
        page.wait_for_timeout(max(0, wait_ms - 600))
        try:
            dismiss_consent(page)
        except Exception:
            pass
        return 200, page.content(), page.url
    finally:
        for x in (page, ctx):
            try:
                if x:
                    x.close()
            except Exception:
                pass


def firecrawl_fetch(url):
    """Last rung: Firecrawl's own fetcher (its IPs get through walls ours do not). (status, html) or (None, None)."""
    import os
    from .sources.firecrawl_agent import API
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return None, None
    r = requests.post(f"{API}/scrape", json={"url": url, "formats": ["html"], "onlyMainContent": False},
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=90)
    if r.status_code != 200:
        return None, None
    d = (r.json() or {}).get("data") or {}
    # 200 from the Firecrawl API only means Firecrawl's own call succeeded -- metadata.statusCode is
    # the ORIGIN's status. Reporting the API's 200 made a dead posting look live: dongku.de's PDF is a
    # 404, WordPress answered with its 404 page, and that page carried enough of the job title for
    # decide() to call it live (confirmed live 2026-09-16).
    md = d.get("metadata") or {}
    code = md.get("statusCode")
    return (int(code) if isinstance(code, (int, str)) and str(code).isdigit() else 200), d.get("html") or d.get("rawHtml")


def verify_one(session, url, title, rungs=("http", "render")):
    """Escalate through `rungs` until one of them can actually see the posting. Returns
    dict(verify_status, verify_http, verify_note, method, city, plz, loc_source, final_url).
    `method` names the rung that produced the verdict, so a caller can always tell a real check from
    an assertion. Split rungs on purpose: the http rung is thread-safe, the render rung is not."""
    out = {"city": None, "plz": None, "loc_source": None, "final_url": url}
    html, code, method = None, None, "none"
    st, http, note = "error", None, "not checked"
    allow_render, allow_firecrawl = "render" in rungs, "firecrawl" in rungs
    forced = bool(FRAGMENT_URL.search(url or ""))

    if "http" in rungs and not forced:
        method = "http"
        try:
            r = session.get(url, headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"},
                            timeout=40, allow_redirects=True)
            code, out["final_url"] = r.status_code, r.url
            if IS_PDF.search(url or "") or "application/pdf" in (r.headers.get("content-type") or "").lower():
                # A posting that IS a PDF (Klinikum Passau, stadtklinik-diako, augenklinik-muenchen,
                # dongku) has no HTML title to match and no DOM to render -- the file answering 200 is
                # the whole liveness question. Escalating it to a browser only produced "render failed".
                html = ""
                st, http, note = ("live", code, "pdf reachable") if code == 200 else decide(code, "", title)
            else:
                html = r.text
                st, http, note = decide(code, html, title)
        except requests.RequestException as e:
            st, http, note = decide(None, None, title, exc_name=type(e).__name__)
        if st == "live" or (st == "gone" and http in (404, 410)) or IS_PDF.search(url or ""):
            out.update(verify_status=st, verify_http=http, verify_note=note, method="http")
            out["city"], out["plz"], out["loc_source"] = extract_location(html)
            return out
    elif forced:
        st, http, note = "error", None, "url carries its id in the fragment -- HTTP cannot see it"

    if allow_render:
        try:
            code, html, out["final_url"] = render(url)
            rst, rhttp, rnote = decide(code, html, title)
            method = "playwright"
            if rst == "live" or rst == "gone":
                out.update(verify_status=rst, verify_http=rhttp, verify_note=f"{rnote} [rendered]", method=method)
                out["city"], out["plz"], out["loc_source"] = extract_location(html)
                return out
            st, http, note = rst, rhttp, rnote
        except Exception as e:
            note = f"{note}; render failed: {type(e).__name__}"

    if allow_firecrawl:
        try:
            code, html = firecrawl_fetch(url)
            if html:
                fst, fhttp, fnote = decide(code, html, title)
                out.update(verify_status=fst, verify_http=fhttp, verify_note=f"{fnote} [firecrawl]", method="firecrawl")
                out["city"], out["plz"], out["loc_source"] = extract_location(html)
                return out
        except Exception as e:
            note = f"{note}; firecrawl failed: {type(e).__name__}"

    out.update(verify_status=st, verify_http=http, verify_note=note, method=method)
    if html:
        out["city"], out["plz"], out["loc_source"] = extract_location(html)
    return out


VERIFY_FIELDS = ("posting_id", "verify_status", "verify_http", "verified_at", "verify_note")


def _vrow(r, res):
    return {"posting_id": r["posting_id"], "verify_status": res["verify_status"], "verify_http": res["verify_http"],
            "verified_at": _now(), "verify_note": res["verify_note"], "method": res["method"],
            "city": res["city"], "plz": res["plz"], "loc_source": res["loc_source"], "final_url": res["final_url"]}


def verify_all(rows, workers=6, log=print, render=True, firecrawl=False):
    """rows: dicts with posting_id, source_url, external_url, title.

    Two passes on purpose: the HTTP rung is threaded, the Playwright rung is not -- crawlers.portals
    keeps ONE sync_playwright browser and the sync API belongs to the thread that started it, so
    everything HTTP could not decide is collected first and rendered in a single sequential pass.
    Returns the five `verify` op fields plus method/city/plz/loc_source (strip with VERIFY_FIELDS
    before pushing to the ingest function)."""
    s = requests.Session()
    out, todo, done = [], [], 0

    def http_one(r):
        url = r.get("external_url") or r.get("source_url")
        return r, verify_one(s, url, r["title"], rungs=("http",))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(http_one, r) for r in rows]):
            r, res = f.result()
            done += 1
            if res["verify_status"] == "live" or (res["verify_status"] == "gone" and res["verify_http"] in (404, 410)):
                out.append(_vrow(r, res))
            else:
                todo.append((r, res))
            if done % 250 == 0:
                log(f"  http {done}/{len(rows)} (escalating {len(todo)})")
    log(f"  http pass done: {len(out)} decided, {len(todo)} need a browser")

    if render and todo:
        rest = []
        for i, (r, res0) in enumerate(todo, 1):
            url = r.get("external_url") or r.get("source_url")
            try:
                res = verify_one(s, url, r["title"], rungs=("render",))
            except Exception as e:
                res = {**res0, "verify_note": f"{res0.get('verify_note')}; render crashed {type(e).__name__}"}
            if res["verify_status"] in ("live", "gone"):
                out.append(_vrow(r, res))
            else:
                rest.append((r, res))
            if i % 25 == 0:
                log(f"  render {i}/{len(todo)}")
        try:
            from crawlers.portals import _close_browser
            _close_browser()
        except Exception:
            pass
        todo = rest
        log(f"  render pass done: {len(todo)} still undecided")

    if firecrawl and todo:
        rest = []
        for r, res0 in todo:
            url = r.get("external_url") or r.get("source_url")
            try:
                res = verify_one(s, url, r["title"], rungs=("firecrawl",))
            except Exception as e:
                res = {**res0, "verify_note": f"{res0.get('verify_note')}; firecrawl crashed {type(e).__name__}"}
            if res["verify_status"] in ("live", "gone"):
                out.append(_vrow(r, res))
            else:
                rest.append((r, res))
        todo = rest
        log(f"  firecrawl pass done: {len(todo)} still undecided")

    for r, res in todo:                       # nothing could see it -- record that, do not invent a verdict
        out.append(_vrow(r, res))
    return out
