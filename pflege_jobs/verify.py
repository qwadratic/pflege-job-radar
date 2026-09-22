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
                          r"job is no longer|no longer available|position has been filled|page not found|"
                          # A bare "404" alone matches a DOM id, CSS class, asset hash or phone number
                          # on almost any page (confirmed live 2026-09-18: 361/2938 postings, 12%, sit
                          # on a host where a stray "404" is present on nearly every page) -- only a
                          # real "error 404"/"404 ... not found/Fehler/Seite" phrase counts.
                          r"error\s*404\b|404\s*(?:[-–:]\s*)?(?:not\s*found|fehler|seite)", re.I)
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
    if not toks:
        # Zero evidence, not confirmation: the two commonest nursing titles ("Pflegefachkraft
        # (m/w/d)", "Gesundheits- und Krankenpfleger (m/w/d)") lose every token here, so a 200
        # response -- including an explicit "nicht mehr verfügbar" page or a bounce to the job
        # list -- used to be read as "live" outright and skip the render/firecrawl escalation
        # entirely (confirmed live 2026-09-18: 134/3911 rows, 3.4%, decided this way, all "live").
        return "error", 200, "title has no matchable token"
    hit = sum(1 for t in toks if t in body)
    if hit == 0 and GONE_MARKERS.search(body):
        return "gone", 200, "200 but title missing + gone marker"
    if hit == 0:
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
# A site-directory idiom ("Besuchen Sie zum Standort Bremen", "unsere/weitere/alle/andere Standort...")
# names a DIFFERENT site than the one this page is actually about -- _EINSATZORT's bare (no colon/
# dash) form cannot tell that shape from a genuine "Standort: Bremen" label by punctuation alone, and
# it was the single biggest source of the ~110/day false city mismatches this rung produced
# (confirmed live 2026-09-18: "zum Standort Bremen" read as the posting's own location).
_EINSATZORT_IDIOM = re.compile(r"\b(?:zum|zur|unsere|weitere|alle|andere)\s+$", re.I)


def _scalar(v):
    """A JSON-LD PostalAddress field that arrives as a one-item list instead of a string (confirmed
    live 2026-09-21: www.komm-ins-klinikland.de ships {"postalCode": ["97318"], "addressLocality":
    ["Kitzingen"]}). Unwrapped here rather than defended against downstream: a list inside the
    (city, plz) tuple below is unhashable, and the TypeError took down the WHOLE daily verify run --
    5 pages aborted the re-check of all 2560 open postings (run 109, 2026-09-21)."""
    return (v[0] if v else None) if isinstance(v, list) else v


def _walk_jsonld(node, out):
    """Collect one (city, plz) per JobPosting jobLocation in a JSON-LD document (dict, list or
    @graph) -- (None, None) when that JobPosting's own jobLocation is a list of more than one
    DISTINCT address (a real posting open at several sites at once, e.g. karriere.ge-passau.de)
    rather than guessing by taking the list's first entry, which attributed the wrong site's city to
    a posting that names a different one."""
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
        addrs = []
        for pl in (loc if isinstance(loc, list) else [loc]):
            addr = (pl or {}).get("address") if isinstance(pl, dict) else None
            if isinstance(addr, dict):
                addrs.append((_scalar(addr.get("addressLocality")), _scalar(addr.get("postalCode"))))
        # Pick the one non-blank address, not addrs[0] -- a blank placeholder entry (empty address
        # object) ahead of the real one in the list must not itself count as "ambiguous" or win by
        # position; distinctness (and which address survives) is computed only over non-blank entries.
        distinct = {a for a in addrs if a[0] or a[1]}
        out.append(next(iter(distinct)) if len(distinct) == 1 else (None, None))
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
    s = str(s).replace("\xa0", " ")
    # A JSON-LD addressLocality sometimes carries "PLZ City" as one field instead of splitting it
    # across addressLocality/postalCode (confirmed live 2026-09-18: jobs.klinikum-gap.de) -- left
    # in, the PLZ token satisfies none of the stop checks below (a bare digit run is not
    # _CITY_STOP and not lowercase) and rides along as if it were part of the place name, which
    # then never matches the plain city name stored on the posting and inverts every mismatch check.
    s = re.sub(r"^\d{5}\s+", "", s)
    out = []
    for i, p in enumerate(s.split()):
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


def _placeable(city, plz, towns):
    """Is this string a place we can actually place? Same self-validating trick city_from_url uses:
    in_bavaria() must reach a verdict on it. Rejects what the label regexes pick up off page furniture
    -- 'Campus Großhadern', 'Box', 'Karte', 'Klinikum Freising' -- without needing a list of them
    (2026-09-17: 535 of the day's 855 reported city mismatches came from an 'Einsatzort:' label, and
    most of them were not cities at all)."""
    if towns is None:
        return True
    from .sources.career_crawl import in_bavaria
    return in_bavaria(city, plz, None, towns) is not None


def extract_location(html, towns=None):
    """(city, plz, source) straight off the posting page. JSON-LD first (a real JobPosting field),
    then an 'Einsatzort:' label, then a bare '12345 Ort' pair -- see TRUSTED_LOC for which of those a
    caller may act on. Pass `towns` to have the two label-scraped sources validated as real places;
    without it they are returned unchecked. (None, None, None) when the page says nothing."""
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
    m = next((x for x in _EINSATZORT.finditer(text)
              if not _EINSATZORT_IDIOM.search(text[max(0, x.start() - 20):x.start()])), None)
    if m:
        c = _clean_city(m.group(1))
        if c and _placeable(c, None, towns):
            return c, None, "einsatzort"
    m = _PLZ_ORT.search(text)
    if m:
        c = _clean_city(m.group(2))
        if _placeable(c, m.group(1), towns):
            return c, m.group(1), "plz_ort"
    return None, None, None


# --- escalation rungs ---------------------------------------------------------------------------
# ".../jobs#/detail/123", ".../stellen#jobid=88": the id never reaches the server, so HTTP would judge
# the bare list page instead of the posting. A plain in-page anchor ("#content") carries no id and is
# left on the HTTP rung.
# Comma joins two key=value pairs in pi_asp.py's own fragment shape ("#position,id=<pid>") -- the
# id-bearing alternatives below need it in their character class or that exact shape falls through
# unforced onto the http rung, which then judges the bare board root instead of the posting
# (confirmed live 2026-09-18: the 3 Helios P&I boards, 51 open rows).
FRAGMENT_URL = re.compile(r"#/\w|#[\w\-,]+=|#[\w\-=/,]*\d")
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


# P&I LOGA "bewerber-web" (GWT) boards have NO per-posting page at all: every posting's URL is the
# same list URL plus a '#title=' fragment the app ignores on load, and clicking a row on the regiomed
# wildcard board does nothing (see pflege_jobs/sources/pi_asp.py's docstring). Fetching such a URL can
# therefore never show "the posting" -- the only honest liveness question is whether the board still
# lists that title. The list only renders under networkidle + scrolling, which is why a plain render()
# saw an empty 7KB GWT shell and left 38 rows stuck on 'error' (2026-09-16).
PI_LOGA = re.compile(r"/bewerber-web/", re.I)
_BOARD_TITLES = {}


def board_titles(url):
    """Titles the P&I LOGA board at `url` currently lists. Cached per verify_all() run (cleared
    there, see reset_board_titles_cache): one render answers every posting on that board."""
    base = url.split("#", 1)[0]
    if base in _BOARD_TITLES:
        return _BOARD_TITLES[base]
    from crawlers.portals import fetch_page
    from .sources.pi_asp import _list_rows
    page = ctx = None
    try:
        # Reuses crawlers.portals' single shared browser (render() above does the same) --
        # opening a SECOND sync_playwright() in the same thread that already holds it used to
        # raise on every call after the first, silently disabling this rung for the rest of the
        # process (confirmed live 2026-09-18: the regiomed P&I LOGA board, 40 postings).
        page, ctx = fetch_page(base, wait_ms=0, timeout_ms=60000)
        page.wait_for_load_state("networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        for _ in range(5):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(500)
        titles = {norm_text(r["title"]) for r in _list_rows(page) if r.get("title")}
    except Exception:
        # Do not cache a result a caught exception produced -- an empty set here used to look
        # exactly like "the board really has no postings" and silently disable this rung for
        # every later call in the same process.
        return set()
    finally:
        for x in (page, ctx):
            try:
                if x: x.close()
            except Exception:
                pass
    _BOARD_TITLES[base] = titles
    return titles


def reset_board_titles_cache():
    """Clear the per-process board_titles() cache -- called once per verify_all() run so a stale
    snapshot from an earlier run cannot mark a newly-added posting gone or a removed one live."""
    _BOARD_TITLES.clear()


def _slug(u):
    p = [x for x in (u or "").split("?")[0].split("#")[0].rstrip("/").split("/") if x]
    return p[-1].lower() if p else ""


def _bounced_to_list(url, final_url, st=None):
    """A posting that quietly redirects to the board's job LIST is gone, even though it answers 200 and
    carries no "nicht mehr verfügbar" text (confirmed live 2026-09-16: LMU's referral portal sends
    /stellenanzeigen/<slug> to /jobs). Decided from the URL shape alone -- the redirect target is
    almost always a job-list page, which contains one of the posting's own three title tokens often
    enough that a caller-supplied st=="live" used to veto this check outright, permanently freezing
    a dead posting as live (confirmed live 2026-09-18: 5 medbo.de + 3 lmu-klinikum.de postings stuck
    this way). `st` is accepted only so existing callers do not need updating; it is never consulted.
    An ordinary canonical/locale redirect is not mistaken for a dead posting because the slug is
    still a substring of the (unchanged) final URL, not because the title happened to match."""
    if not final_url or final_url == url:
        return False
    s = _slug(url)
    return bool(s) and len(s) > 8 and s not in (final_url or "").lower()


def verify_one(session, url, title, rungs=("http", "render"), towns=None):
    """Escalate through `rungs` until one of them can actually see the posting. Returns
    dict(verify_status, verify_http, verify_note, method, city, plz, loc_source, final_url).
    `method` names the rung that produced the verdict, so a caller can always tell a real check from
    an assertion. Split rungs on purpose: the http rung is thread-safe, the render rung is not."""
    out = {"city": None, "plz": None, "loc_source": None, "final_url": url}
    html, code, method = None, None, "none"
    st, http, note = "error", None, "not checked"
    allow_render, allow_firecrawl = "render" in rungs, "firecrawl" in rungs
    forced = bool(FRAGMENT_URL.search(url or ""))

    if PI_LOGA.search(url or ""):
        # There is no detail page on these boards, so every OTHER rung fetches the board LIST -- which
        # contains every title, including the removed ones, so decide() finds the posting's own tokens
        # on it and answers "live" forever (confirmed live 2026-09-18: 51 Helios rows carry
        # "title tokens 3/3 [rendered]" from exactly that fall-through). The board list is the only
        # rung allowed to answer here; when it cannot, the honest answer is "undecided".
        if not allow_render:
            out.update(verify_status="error", verify_http=None, method="board_list",
                       verify_note="P&I LOGA: only the rendered board list can see this posting")
            return out
        titles = board_titles(url)
        if titles:
            hit = norm_text(title or "") in titles
            out.update(verify_status="live" if hit else "gone", verify_http=200, method="board_list",
                       verify_note=("still listed on the board" if hit else "no longer listed on the board")
                       + " (P&I LOGA: the list IS the posting, there is no detail page)")
        else:
            # Drift alarm: the whole board goes to crawl_issues under method 'board_list' on the next
            # mode=verify run, instead of one unreadable render quietly expiring or reviving 91 rows.
            out.update(verify_status="error", verify_http=None, method="board_list",
                       verify_note="P&I LOGA board listed no titles -- board empty or the list rung broke")
        return out

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
        if _bounced_to_list(url, out["final_url"], st):
            st, http, note = "gone", code, f"redirected to {out['final_url'][:80]} (posting path gone)"
        if st == "live" or (st == "gone" and http in (404, 410)) or st == "gone" and "redirected" in (note or "") or IS_PDF.search(url or ""):
            out.update(verify_status=st, verify_http=http, verify_note=note, method="http")
            out["city"], out["plz"], out["loc_source"] = extract_location(html, towns)
            return out
    elif forced:
        st, http, note = "error", None, "url carries its id in the fragment -- HTTP cannot see it"

    if allow_render:
        try:
            code, html, out["final_url"] = render(url)
            rst, rhttp, rnote = decide(code, html, title)
            method = "playwright"
            if _bounced_to_list(url, out["final_url"], rst):
                rst, rhttp, rnote = "gone", code, f"redirected to {out['final_url'][:80]} (posting path gone)"
            if rst == "live" or rst == "gone":
                out.update(verify_status=rst, verify_http=rhttp, verify_note=f"{rnote} [rendered]", method=method)
                out["city"], out["plz"], out["loc_source"] = extract_location(html, towns)
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
                out["city"], out["plz"], out["loc_source"] = extract_location(html, towns)
                return out
        except Exception as e:
            note = f"{note}; firecrawl failed: {type(e).__name__}"

    out.update(verify_status=st, verify_http=http, verify_note=note, method=method)
    if html:
        out["city"], out["plz"], out["loc_source"] = extract_location(html, towns)
    return out


# --- board-membership retirement (TASK-87) ------------------------------------------------------
# Everything above answers "is this URL still reachable" -- a junk/duplicate row whose page nobody
# pulled down stays verify_status='live' forever even after it drops off the board (M7: Münchberg's
# two .io duplicates were still status='open' 15 days after their last observation). The board
# itself, not the posting's own page, is the only source that can say a posting left it.
def board_absent_gone(open_rows, board_urls, walk_ok):
    """verify-shaped rows (VERIFY_FIELDS) that retire postings a SUCCESSFUL board walk no longer
    lists. Feeds the same EdgeSink 'verify' op verify_all() already posts -- the edge function
    already closes a posting on verify_status='gone' (edge/pflege-ingest/index.ts:63), so this needs
    no new write path, only a caller that gathers board_urls and walk_ok.

    open_rows: this board's currently-open postings, each carrying 'posting_id' and 'external_url'
      (falls back to 'source_url').
    board_urls: every URL the walk just returned -- board MEMBERSHIP as of right now, not liveness.
    walk_ok: True only when the walk itself completed (TASK-73 AC#6 / TASK-14 AC#2: a failed or
      safety-ceiling-truncated walk read less of the board than exists, so absence there proves
      nothing -- see app/crawl.py's crawl_issues kinds 'error'/'truncated'). The caller decides this
      from its own signals; False always returns [] here, so a walk that did not finish can never
      retire a posting no matter what the caller forgets to check elsewhere.

    Deliberately silent on 'empty' (0 rows, no transport error): a board that is genuinely down to
    zero real postings should retire everything on it (that IS the M7 gap), but a board read at the
    WRONG url (a registry defect, not a walk failure) also comes back 0 rows with no error -- this
    function cannot tell those apart from board_urls alone, so that call is the caller's, made with
    whatever it additionally knows about the board's own registry state."""
    if not walk_ok:
        return []
    seen = {u for u in (board_urls or []) if u}
    return [{"posting_id": r["posting_id"], "verify_status": "gone", "verify_http": None, "verified_at": _now(),
             "verify_note": "absent from a successful board walk (board membership, not URL liveness)"}
            for r in open_rows if (r.get("external_url") or r.get("source_url")) not in seen]


VERIFY_FIELDS = ("posting_id", "verify_status", "verify_http", "verified_at", "verify_note")


def _vrow(r, res):
    return {"posting_id": r["posting_id"], "verify_status": res["verify_status"], "verify_http": res["verify_http"],
            "verified_at": _now(), "verify_note": res["verify_note"], "method": res["method"],
            "city": res["city"], "plz": res["plz"], "loc_source": res["loc_source"], "final_url": res["final_url"]}


def verify_all(rows, workers=6, log=print, render=True, firecrawl=False, towns=None):
    """rows: dicts with posting_id, source_url, external_url, title.

    Two passes on purpose: the HTTP rung is threaded, the Playwright rung is not -- crawlers.portals
    keeps ONE sync_playwright browser and the sync API belongs to the thread that started it, so
    everything HTTP could not decide is collected first and rendered in a single sequential pass.
    Returns the five `verify` op fields plus method/city/plz/loc_source (strip with VERIFY_FIELDS
    before pushing to the ingest function)."""
    reset_board_titles_cache()
    s = requests.Session()
    out, todo, done = [], [], 0

    def http_one(r):
        url = r.get("external_url") or r.get("source_url")
        try:
            return r, verify_one(s, url, r["title"], rungs=("http",), towns=towns)
        except Exception as e:
            # One unparseable page must not end the pass. as_completed().result() re-raises into the
            # caller, so a single malformed JSON-LD address aborted the whole daily re-verification
            # and left every open posting on its old verdict for three days (run 109, 2026-09-21).
            # The row is escalated carrying the crash, so it is still re-checked and still reaches
            # crawl_issues if the browser cannot see it either -- never absorbed as a verdict.
            return r, {"verify_status": "error", "verify_http": None, "method": "http",
                       "verify_note": f"http rung crashed: {type(e).__name__}: {str(e)[:120]}",
                       "city": None, "plz": None, "loc_source": None, "final_url": url}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(http_one, r) for r in rows]):
            r, res = f.result()
            done += 1
            # Any "gone" the http rung already settled on -- a real 404/410, an explicit gone
            # marker, or a redirect-to-list bounce -- is final; only escalating the narrower
            # (404, 410) subset used to hand a genuinely-decided gone verdict to the render rung,
            # which then flipped it back to "live" as soon as the rendered page carried one title
            # token (confirmed live 2026-09-18: the redirect-bounce and gone-marker shapes both did
            # this in run 90's escalated rows).
            if res["verify_status"] in ("live", "gone"):
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
                res = verify_one(s, url, r["title"], rungs=("render",), towns=towns)
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
                res = verify_one(s, url, r["title"], rungs=("firecrawl",), towns=towns)
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
