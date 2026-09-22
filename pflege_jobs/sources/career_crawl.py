"""Career-portal crawler (source employer_ats, precedence 2).

Per clinic seed: BFS within the career host(s), depth <= 2, page budget; every fetched page is checked for
schema.org JobPosting JSON-LD (the de-facto standard emitted by softgarden, d.vinci, rexx, concludis, SmartRecruiters,
Personio, mein-check-in...). Pages without JSON-LD but with a title + "Bewerben" are parsed heuristically
(title = <h1>/<title>, location from text) and flagged `parse='heuristic'`.
Bavaria detection: jobLocation.addressRegion in {Bayern, Bavaria} OR postal code in Bavarian ranges OR city in the
Bavarian town list (registry + Arbeitsagentur) -- written onto each row as in_bavaria, not used to drop rows here.
robots.txt is honoured (urllib.robotparser); throttle per host.
"""
import html as _html
import json
import re
import time
import urllib.robotparser
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import requests

from .. import config as C
from ..classify import (canonical_job_url, classify_employer, classify_role, content_hash, department_hint,
                        employer_norm, enrich_description, fuzzy_key, norm_text, qualification_hint)
from ..section import pick_nursing_link

SOURCE_ID = C.SOURCES["employer_ats"]["source_id"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 pflege-jobs-crawler"
LINK_OK = re.compile(r"job|stelle|vacanc|position|karriere|career|bewerb|angebot|offer|posting|/p/|/de/", re.I)
JOB_HREF = re.compile(r"/detail/|/detailansicht/|/job/|/jobad\?|/jobs?/[^/?]*\d|/karriere/jobs/|/stellenangebot|/stellenanzeige|/vacanc|/position/|jobid|job_id|jobdetail|/p/|[?&]id=\d|/de/jobs/\d|/jobs/\d", re.I)
JOB_TEXT = re.compile(r"\((?:m|w|d|x|i|gn|a)\s?[/|\\*]\s?(?:m|w|d|x|i|gn|a)(?:\s?[/|\\*]\s?(?:m|w|d|x|i|gn|a))?\)|\b[mwd]/[mwd]/[mwdx]\b|\*in\b|:in\b", re.I)
LIST_NAV = re.compile(r"weiter|nächste|next|mehr laden|alle stellen|page|seite|pflege|krankenpflege|medizin|berufsgruppe|fachbereich|kategorie|filter", re.I)
LINK_BAD = re.compile(r"\.(pdf|jpe?g|png|gif|svg|css|js|zip|docx?|xlsx?)(\?|$)|mailto:|tel:|javascript:|#|login|logout|datenschutz|impressum|agb|cookie|newsletter|facebook|instagram|linkedin|xing|youtube|twitter|share|print"
                       # umantis: /Jobs/<n> is always its own paginated listing (never a posting) --
                       # language-switcher anchors on that listing self-link to it in every UI language
                       # ("Zum Hauptinhalt springen", "Erweiterte Suche", ...), and InitiativeApplication
                       # is a generic "apply speculatively" CTA, not a posting either (confirmed live on
                       # all 4 direct-host umantis boards: both otherwise pass JOB_HREF's /vacanc match).
                       r"|/Jobs/\d+(?:[?&]|$)|InitiativeApplication", re.I)
PAGINATE = re.compile(r"[?&](page|p|seite|start|offset|pageNo|pagenr)=\d+", re.I)
# Bavarian PLZ ranges: 637xx-639xx (Aschaffenburg), 80xxx-87xxx, 881[3-7]xx (Lindau), 892xx-895xx (Neu-Ulm/Günzburg/Dillingen), 90xxx-97xxx.
# Excluded even though they fall inside those broad ranges: 895xx (Heidenheim/Giengen, Baden-
# Württemberg), 978xx/979xx (Wertheim/Bad Mergentheim, BW), 9651x/9652x (Sonneberg, Thüringen --
# the only non-Bavarian pocket inside the wider 96xxx Bavarian block), 87491 (Jungholz, Austria).
BAV_PLZ = re.compile(r"^(?!895\d\d$|978\d\d$|979\d\d$|9651\d$|9652\d$|87491$)"
                     r"(63[7-9]\d\d|8[0-7]\d{3}|881[3-7]\d|89[2-5]\d\d|9[0-7]\d{3})$")
NON_BAV_CITIES = {"frankfurt", "frankfurt (oder)", "gießen", "marburg", "bad berka", "leipzig", "berlin", "hamburg", "stuttgart", "ulm", "köln",
                  "düsseldorf", "hannover", "dresden", "erfurt", "kassel", "wiesbaden", "mainz", "heidelberg", "mannheim", "karlsruhe", "freiburg",
                  # seen live 2026-09-16 on postings that reached the board with no PLZ to decide on
                  "düren", "rendsburg", "eckernförde", "bochum", "duisburg", "schwerin", "bad saarow", "ludwigshafen", "haldensleben",
                  "oberhausen", "hameln", "warendorf", "hildesheim", "aschersleben", "bernburg", "halberstadt", "bremerhaven", "bremen",
                  "goslar", "holzminden", "preetz", "ratzeburg", "eutin", "anklam", "stolzenau", "heiligenhafen", "geestland", "schönebeck"}


def _jsonld_jobpostings(html):
    out = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            try: data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
            except Exception: continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                t = node.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t): out.append(node)
                for v in node.values():
                    if isinstance(v, (dict, list)): stack.append(v)
            elif isinstance(node, list): stack.extend(node)
    return out


def _strip(html):
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", html, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", html)
    # html.unescape decodes every entity (umlauts, punctuation, ...) instead of the previous
    # regex's partial handling (&nbsp;/&amp; only, every other entity replaced by a bare space) --
    # that destroyed umlauts in titles/descriptions on any board that entity-encodes its markup
    # (confirmed live: 111 rows/run from deutsches-herzzentrum-muenchen.de, 30 titles/run from
    # waldkrankenhaus.de losing "(m/w/d)" to "&#40;/&#41;"). &nbsp; still folds to a plain space
    # rather than U+00A0, matching the previous, deliberate whitespace-collapsing behaviour.
    txt = _html.unescape(txt).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", txt)).strip()


def _registrable_domain(netloc):
    """Naive eTLD+1 (last two dot-separated labels, port stripped), same notion as
    crawlers.vendor_adapters._registrable_domain -- this crawler only ever visits .de/.com/.io
    hospital and ATS domains, so a public-suffix-list dependency buys nothing here.

    The same-board checks below used to strip exactly ONE leading label (h.split('.', 1)[-1]),
    which is only the registrable domain when h actually has a subdomain: for a bare two-label
    apex host ('kbo-iak.de') it degenerated to the bare TLD 'de', so every .de host in the world
    passed the suffix compare (TASK-79).
    """
    host = (netloc or "").split(":")[0].lower()
    return ".".join(host.split(".")[-2:])


def _location(jp):
    locs = jp.get("jobLocation") or []
    if isinstance(locs, dict): locs = [locs]
    out = []
    for l in locs:
        a = (l or {}).get("address") or {}
        if isinstance(a, str): out.append({"city": a, "plz": None, "region": None}); continue
        out.append({"city": a.get("addressLocality"), "plz": str(a.get("postalCode") or "").strip() or None, "region": a.get("addressRegion")})
    return out


GENERIC_PREFIX = {"bad", "sankt", "st", "st.", "markt", "neu", "ober", "unter", "gross", "groß", "klein"}

# Registry-parse junk stems (krankenhausplan.py's PDF name-splitter occasionally writes one of
# these as a clinic's "town" when its own name-block parse fails) -- never a real place, even when
# one ends up sitting verbatim in the registry-derived `towns` set passed into in_bavaria(). Without
# this, ordinary page furniture ("Klinikum Nürnberg", "GmbH & Co. KG") passed the bare-town checks
# below as if it named a real Bavarian municipality (confirmed live 2026-09-18).
_TOWN_JUNK = {"klinik", "kliniken", "klinikum", "krankenhaus", "krankenhäuser", "co", "co.", "gmbh", "ggmbh",
             "kg", "ag", "zentrum", "haus", "stiftung", "gesundheit", "gemeinnützige", "gemeinnuetzige"}


def _load_ambiguous_stems():
    """Bare town-name stems (data/geo/ambiguous_stems.txt, built by tools/build_geo_table.py from
    the Destatis municipality list) that occur in 2+ Bundeslaender -- a bare first-token match on
    one of these is a coin flip between a real Bavarian town and a same-named town somewhere else
    in Germany, and must not decide Bavaria on its own."""
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent.parent / "data" / "geo" / "ambiguous_stems.txt"
    try:
        with open(path, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip() and not line.startswith("#")}
    except OSError:
        return set()


_AMBIGUOUS_STEMS = _load_ambiguous_stems()

# the 16 Bundeslaender, for reading a region out of a "Ort, Land, Deutschland" city string
LAND_RX = re.compile(r"bayern|bavaria|baden-württemberg|baden-wuerttemberg|hessen|thüringen|thueringen|sachsen|"
                     r"sachsen-anhalt|brandenburg|nordrhein-westfalen|niedersachsen|berlin|hamburg|bremen|"
                     r"rheinland-pfalz|saarland|schleswig-holstein|mecklenburg-vorpommern", re.I)


# Job URLs regularly end in the city the job is actually in
# (".../10240-pflegefachkraft-neurologie-fruehrehabilitation-in-oberhausen"). That is the SOURCE
# naming the location, so it outranks a city the crawler substituted from the seed clinic -- which is
# the only thing that catches the AMEOS shape, where the page states no location anywhere and 22 of 26
# new rows a night carry the seed clinic's Bavarian town while their own URL says Oberhausen,
# Haldensleben, Eutin (measured 2026-09-17).
_URL_CITY = re.compile(r"-in-([a-zäöüß][a-zäöüß0-9\-]{3,})(?:\.html?)?/?$", re.I)
_URL_PCT = {"%c3%bc": "ü", "%c3%a4": "ä", "%c3%b6": "ö", "%c3%9f": "ß", "%c3%9c": "ü", "%c3%84": "ä", "%c3%96": "ö"}


def city_from_url(url, towns):
    """The city a job URL names in its own slug, but only when it is a place we can actually place:
    in_bavaria() must return True or False for it. That rejects the slugs that are not cities at all
    ("-in-teilzeit", "-in-vollzeit") without needing a list of them."""
    u = (url or "").split("?")[0].split("#")[0]
    for k, v in _URL_PCT.items():
        u = u.replace(k, v).replace(k.upper(), v)
    m = _URL_CITY.search(u)
    if not m:
        return None
    city = m.group(1).replace("-", " ").strip()
    if in_bavaria(city, None, None, towns) is not None:
        return city
    # the registry writes some towns in a form a slug never uses ("Neuburg/Donau" vs
    # "neuburg-an-der-donau", "Garmisch-Partenkirchen" vs "garmisch-partenkirchen"), so compare on a
    # form where the separators and the connector words are gone before giving up
    return city if _canon_town(city) in {_canon_town(t) for t in towns} else None


_TOWN_CONNECT = re.compile(r"\b(an|am|im|bei|der|die|ob|vor|auf|a|i|d)\b")


def _canon_town(s):
    s = re.sub(r"[\-/,.()]+", " ", norm_text(s or ""))
    return " ".join(_TOWN_CONNECT.sub(" ", s).split())


def in_bavaria(city, plz, region, towns):
    # A malformed upstream row can carry a one-item list instead of a scalar (seen live 2026-09-09,
    # inbox_id 13576: {"plz": ["97318"], "city": ["Kitzingen"]}) -- one bad row must never crash the
    # whole drain for every other pending row behind it.
    if isinstance(plz, list): plz = plz[0] if plz else None
    if isinstance(city, list): city = city[0] if city else None
    if isinstance(region, list): region = region[0] if region else None
    # Some sources put the Bundesland in the city string instead of its own field ("Coburg, Bayern,
    # Deutschland" -- every row of the P&I LOGA regiomed board). That is the source stating the region,
    # so read it rather than throw it away; matched per comma-part, never as a substring, so a town
    # like "Bad Bayersoien" cannot pass for "Bayern".
    if not region and city and "," in str(city):
        for part in str(city).split(",")[1:]:
            p = norm_text(part)
            if re.fullmatch(r"bayern|bavaria|by", p) or LAND_RX.fullmatch(p):
                region = part.strip()
                break
    if region and re.search(r"bayern|bavaria|^by$", str(region).strip(), re.I): return True
    if region and re.search(r"hessen|thüringen|sachsen|brandenburg|baden|württemberg|nordrhein|niedersachsen|berlin|hamburg|rheinland|saarland|schleswig|mecklenburg|bremen|^(nw|he|bw|th|sn|ni|rp|sh|mv|bb|hh|hb|be|sl|st)$", str(region).strip(), re.I): return False
    c = norm_text(city or "")
    # a malformed PLZ decides nothing and must not block the city string's own one (seen live:
    # plz='345387' with city='34537 Bad Wildungen', which kept a Hessen posting undecidable)
    if plz and not re.match(r"^\d{5}$", str(plz).strip()):
        plz = None
    # a city string that carries its own PLZ ("34537 Bad Wildungen") is still a PLZ statement
    if not plz and c:
        m = re.match(r"^(\d{5})\b", c)
        if m: plz = m.group(1)
    if plz and BAV_PLZ.match(plz): return True
    if plz and re.match(r"^\d{5}$", plz): return False
    if not c: return None
    if c in NON_BAV_CITIES: return False
    head = c.split(",")[0].strip()
    if head not in _TOWN_JUNK and head in towns: return True
    first = c.split()[0]
    # A bare first-token match is only trusted when the stem is unambiguous across Bundeslaender --
    # otherwise it is a coin flip between a real Bavarian town and a same-named town elsewhere in
    # Germany (see _load_ambiguous_stems' docstring); the full comma-stripped string (checked just
    # above) is exact enough to not need this guard.
    if first not in GENERIC_PREFIX and first not in _TOWN_JUNK and first not in _AMBIGUOUS_STEMS and first in towns: return True
    # "Freiburg im Breisgau" / "Frankfurt am Main" are the same places as the bare names in
    # NON_BAV_CITIES; checked only AFTER `towns`, so a real Bavarian site of the same first word
    # still wins on its own registry entry
    if first in NON_BAV_CITIES: return False
    return None


class Crawler:
    def __init__(self, towns, per_site_pages=5000, list_pages=500, workers=1, sleep=0.25, log=print):
        # per_site_pages/list_pages are a runaway-loop safety ceiling, not a target: the walk's real
        # stop condition is the board's own pagination/job-link queue draining (see _crawl_urls). A
        # board big enough to hit the ceiling is recorded truncated=True, never silently as "done".
        self.towns, self.budget, self.list_budget, self.sleep, self.log = towns, per_site_pages, list_pages, sleep, log
        self.s = requests.Session(); self.s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
        self.robots = {}

    def allowed(self, url):
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if host not in self.robots:
            rp = None
            try:
                # Fetch robots.txt ourselves rather than letting RobotFileParser.read() do it:
                # urllib.robotparser treats a 401/403 on robots.txt as "disallow everything"
                # (rp.disallow_all = True), but a site that 403s its *robots.txt* while serving its
                # real pages fine (confirmed live: recruitingapp-5580.de.umantis.com) is not actually
                # saying "crawl nothing" -- there is no robots.txt to honour, so fail open, matching
                # the except-Exception branch below for a robots.txt that doesn't exist/times out.
                rr = self.s.get(host + "/robots.txt", timeout=15)
                if rr.status_code == 200:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(rr.text.splitlines())
            except Exception:
                rp = None
            self.robots[host] = rp
        rp = self.robots[host]
        try: return rp.can_fetch(UA, url) if rp else True
        except Exception: return True

    def fetch(self, url):
        if not self.allowed(url): return None
        try:
            r = self.s.get(url, timeout=40, allow_redirects=True)
            time.sleep(self.sleep)
            if r.status_code == 200 and "html" in r.headers.get("content-type", ""): return r
        except requests.RequestException:
            return None
        return None

    def sitemap_job_urls(self, url, depth=0, limit=20000):  # loop-safety ceiling, not a board-size cap
        """Return job-like <loc> URLs from a sitemap or sitemap index (recurses one level)."""
        r = None
        try:
            if self.allowed(url):
                r = self.s.get(url, timeout=40); time.sleep(self.sleep)
        except requests.RequestException:
            return []
        if not r or r.status_code != 200: return []
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        if "<sitemapindex" in r.text and depth == 0:
            # Mirrors crawlers.vendor_adapters.find_job_urls' fix for the identical failure mode
            # (TASK-72 AC#2): follow only the job-ish-named children when any exist, else every
            # child -- removing just the old [:10] slice here still left every child gated on
            # "job-ish name OR <=4 total", so an index with 5+ children none of them named (e.g.
            # a plain sitemap-static-N.xml split) silently visited none of them, not just the
            # ones beyond a 10th.
            job_locs = [l for l in locs if re.search(r"job|stelle|karriere|vacanc|post", l, re.I)]
            out = []
            for l in (job_locs or locs):
                out += self.sitemap_job_urls(l, 1, limit)
            return out[:limit]
        return [l for l in locs if JOB_HREF.search(l) or re.search(r"/(job|stelle|vacanc|karriere/[^/]+/[^/]+)", l, re.I)][:limit]

    def _page_hosts_ok(self, u, hosts):
        p = urlparse(u)
        if p.scheme not in ("http", "https") or LINK_BAD.search(u): return False
        return p.netloc in hosts or any(_registrable_domain(p.netloc) == _registrable_domain(h) and ("job" in p.netloc or "karriere" in p.netloc or "softgarden" in p.netloc or "dvinci" in p.netloc) for h in hosts)

    def _section_link(self, r0, hosts):
        """Look at the already-fetched seed page for a confident nursing-section nav/category link
        (see pflege_jobs.section) -- e.g. a "Pflegedienst" menu item, a Berufsgruppe <select> filter
        option, or a data-url-driven category picker (seen on real bespoke WP boards, e.g.
        <option value="6" data-url="https://.../jobs/pflege/">Pflegedienst</option>) -- NOT an
        individual job posting whose title happens to contain "Pflege". Returns an absolute URL or None."""
        nav = []
        for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r0.text, re.S | re.I):
            href, inner = m.group(1), _strip(m.group(2))[:200]
            u = urldefrag(urljoin(r0.url, href))[0]
            if not self._page_hosts_ok(u, hosts): continue
            is_job = bool(JOB_TEXT.search(inner)) or (bool(JOB_HREF.search(u)) and inner and not LIST_NAV.fullmatch(inner.strip()))
            if is_job or not inner: continue
            nav.append((inner, u))
        for m in re.finditer(r'<option\b[^>]*data-url=["\']([^"\']+)["\'][^>]*>(.*?)</option>', r0.text, re.S | re.I):
            href, inner = m.group(1), _strip(m.group(2))[:200]
            u = urldefrag(urljoin(r0.url, href))[0]
            if not self._page_hosts_ok(u, hosts) or not inner: continue
            nav.append((inner, u))
        return pick_nursing_link(nav)

    def _crawl_urls(self, seed, hosts, start_urls, sitemaps, depth_cap=None, prefetched=None, section_confirmed=False):
        """Shared listing-first walk: start_urls (+ sitemaps) -> job links (title-like anchor text or
        job-like href) -> fetch detail pages -> JSON-LD JobPosting or heuristic. depth_cap, if given,
        bounds how many list-page hops beyond start_urls (depth 0) the walk will follow; None keeps the
        original unbounded-depth (page-budget-only) behaviour. prefetched lets a page already fetched by
        the caller (the seed page, when peeking for a section link) be reused instead of re-fetched."""
        prefetched = dict(prefetched or {})
        list_q = deque((u, 0) for u in start_urls)
        seen_lists, job_links, jobs = set(), {}, {}
        stats = {"list_pages": 0, "job_pages": 0, "jobposting_pages": 0, "heuristic_pages": 0}
        while list_q and stats["list_pages"] < self.list_budget:
            url, depth = list_q.popleft()
            url = urldefrag(url)[0]
            if url in seen_lists: continue
            seen_lists.add(url)
            r = prefetched.pop(url, None) or self.fetch(url)
            if not r: continue
            stats["list_pages"] += 1
            html = r.text
            # A category/nav page can itself carry a promotional JobPosting block (or, rarer, BE one) --
            # checked on every fetched list page, same signal job_links' own fetch loop below uses, so
            # a link that only matched JOB_HREF (see below, no gender marker of its own) still becomes
            # a job when the page it points at actually states JobPosting JSON-LD.
            jps = _jsonld_jobpostings(html)
            if jps and r.url not in jobs:
                stats["jobposting_pages"] += 1
                jobs[r.url] = self._from_jsonld(jps[0], r.url, seed, section_confirmed=section_confirmed)
            for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
                href, inner = m.group(1), _strip(m.group(2))[:200]
                u = urldefrag(urljoin(r.url, href))[0]
                if not self._page_hosts_ok(u, hosts): continue
                if u in job_links or u in seen_lists: continue
                # A link is only ever emitted as a posting on its OWN anchor text carrying a
                # posting-shaped signal (a gender marker, "(m/w/d)" and friends -- JOB_TEXT). A link
                # that merely LOOKS job-shaped by its href (JOB_HREF) is not trusted on that alone
                # (TASK-84: 'Pflegedienst'/'Ansprechpartner' nav links and a division/category index
                # page both matched JOB_HREF and neither is a posting) -- it is queued as a list page
                # instead, same as any other candidate list link: its own fetch gets the JSON-LD check
                # above, and its own links get explored, so a real listing one hop behind a
                # category link (confirmed live 2026-09-21: St. Josef Regensburg/36202,
                # /alle-stellenangebote unreachable, 9 of 14 real vacancies never seen -- the category
                # link used to dead-end in job_links instead of ever being queued) is still reached.
                if JOB_TEXT.search(inner) and len(inner) > 6 and not re.search(
                        r"^(mehr|details?|zur stelle|jetzt bewerben|weiterlesen|ansehen)$", inner.strip(), re.I):
                    job_links[u] = inner
                elif JOB_HREF.search(u) or PAGINATE.search(u) or LIST_NAV.search(inner) or LIST_NAV.search(u):
                    # No queue-size ceiling here: `len(seen_lists) + len(list_q) >= list_budget * 2`
                    # used to drop candidate list pages for good, and seen_lists counts every url
                    # POPPED -- including the ones whose fetch failed, which never raise list_pages.
                    # A board with many dead list urls therefore exhausted the queue ceiling with
                    # list_pages still far under list_budget, and lost real pagination it had the
                    # budget to read (confirmed live 2026-09-21: ANregiomed, list_pages 102/500).
                    if depth_cap is None or depth < depth_cap:
                        list_q.append((u, depth + 1))
        for sm in sitemaps:
            for u in self.sitemap_job_urls(sm):
                if u not in job_links and not LINK_BAD.search(u): job_links[u] = ""
        stats["sitemap_links"] = sum(1 for v in job_links.values() if v == "")
        for u, anchor in list(job_links.items())[: self.budget]:
            r = self.fetch(u)
            if not r: continue
            stats["job_pages"] += 1
            jps = _jsonld_jobpostings(r.text)
            if jps:
                stats["jobposting_pages"] += 1
                jobs[r.url] = self._from_jsonld(jps[0], r.url, seed, section_confirmed=section_confirmed)
            else:
                h = self._heuristic(r.text, r.url, seed, anchor, section_confirmed=section_confirmed)
                if h: jobs[r.url] = h; stats["heuristic_pages"] += 1
        out = list(jobs.values())
        stats["job_links_found"] = len(job_links)
        # Truncated, never silently "done": either the list-page queue still had unfetched pages when
        # the safety ceiling hit, or more job links were found than the detail-fetch ceiling allowed.
        stats["truncated"] = bool(list_q) or len(job_links) > self.budget
        return out, stats

    def crawl(self, seed):
        """Listing-first: seed + extra seeds + pagination/filter pages -> job links -> details -> JSON-LD/heuristic.
        seed: {name, kez, career, hosts?, extra_seeds?}

        Section-first: if the seed page itself links to a confident nursing section/category (see
        pflege_jobs.section.pick_nursing_link -- a "Pflegedienst" nav item, a Berufsgruppe/Fachbereich
        filter option, ...), fetch that subtree first (depth<=2 from there, same page budget) so it is
        never skipped for a partial board budget -- then ALWAYS top up with the full board-wide walk
        too, deduped by URL, instead of returning early. Stopping at the section subtree used to be
        precautionary (no observed gap when this path was added); it turned out to silently drop most
        of the board once one was (confirmed live 2026-09-11: Klinikverbund Allgäu's section-first hit
        a narrow "pflegerische Fachweiterbildungen" nav item and returned only 9 of 84 real postings,
        the exact same class of gap crawl_wp_jobs's own equivalent was already fixed for)."""
        hosts = set(seed.get("hosts") or []) | {urlparse(seed["career"]).netloc}
        seed_url = seed["career"]
        r0 = self.fetch(seed_url)
        prefetched = {urldefrag(seed_url)[0]: r0} if r0 else {}
        section_href = self._section_link(r0, hosts) if r0 else None
        section_rows, section_stats = [], None
        if section_href:
            # Every job reached through this subtree came from a confirmed nursing-section nav link
            # (see _section_link above) -- thread that as classify.classify_role's
            # nursing_section_confirmed signal for each one, same structural signal as the other
            # vendor adapters.
            section_rows, section_stats = self._crawl_urls(seed, hosts, [section_href], [], depth_cap=2, section_confirmed=True)
            if section_rows:
                self.log(f"  {seed.get('name', '?')[:30]}: section-first -> {section_href} ({len(section_rows)} rows)")
            else:
                self.log(f"  {seed.get('name', '?')[:30]}: section-first subtree ({section_href}) empty")
        rows, stats = self._crawl_urls(seed, hosts, [seed_url] + list(seed.get("extra_seeds", [])), seed.get("sitemaps", []), depth_cap=None, prefetched=prefetched)
        if section_stats:
            # A truncated section-first sub-walk is still a truncated crawl overall, whether or not
            # it happened to find any rows before hitting its own list_budget -- checked here,
            # unconditionally, so an empty-AND-truncated section subtree (falls through to the
            # section_rows branch below with nothing to merge otherwise) still surfaces it (TASK-72
            # AC#3; the old code only ever merged this -- and only by accident, since it merged
            # nothing at all -- inside the section_rows-truthy branch below).
            stats["truncated"] = stats.get("truncated", False) or section_stats.get("truncated", False)
        if section_rows:
            # The section row WINS a duplicate: it is the same posting plus the knowledge that it was
            # reached through a confirmed nursing section, which is what classify_role needs for a
            # title that does not say "Pflege" on its own ("Advanced Practice Nurses (m/w/d)"). Merging
            # the other way round dropped that signal whenever the full walk also reached the URL, and
            # the posting then classified as nicht_pflege and left the board entirely.
            sec_urls = {r["external_url"] for r in section_rows}
            rows = section_rows + [r for r in rows if r["external_url"] not in sec_urls]
            for k in ("list_pages", "job_pages", "jobposting_pages", "heuristic_pages", "job_links_found", "sitemap_links"):
                stats[k] = stats.get(k, 0) + section_stats.get(k, 0)
        stats["section_first"] = bool(section_href)
        return rows, stats

    def _base(self, url, seed, title, desc, city, plz, region, published, valid, dept, parse, employer=None, section_confirmed=False):
        # seed.get("operator") beats seed["name"]: a shared-hub seed (e.g. ats_seeds.umantis()'s
        # hub_needs_own_host case) sets it precisely when the seed clinic's own name would be wrong
        # for every OTHER site's postings on that same hub -- see ats_seeds.py's umantis() docstring.
        emp = employer or seed.get("operator") or seed["name"]
        e_class, e_rule = classify_employer(emp)
        role, rule = classify_role(title, "", nursing_section_confirmed=section_confirmed)
        enr = {("enr_" + k): v for k, v in enrich_description(desc or "").items()}
        return {
            # source_ref is the (source_id, source_ref)-unique DEDUP identity (sql/001_schema.sql:112)
            # -- canonicalized to the vendor job id (TASK-83) so a job crawled under 2-3 URL shapes
            # upserts onto one observation instead of becoming a duplicate posting per shape.
            # source_url/external_url stay the real, as-crawled link (never rewritten): source_ref is
            # never used to fetch anything (see pflege_jobs/verify.py), only to key identity.
            "source_id": SOURCE_ID, "source_ref": canonical_job_url(url), "source_url": url, "observed_at": datetime.now(timezone.utc).isoformat(),
            "title": title, "employer_name": emp, "employer_name_norm": employer_norm(emp),
            "employer_class": "clinic" if e_class != "clinic" else e_class, "employer_class_rule": e_rule if e_class == "clinic" else f"registry_seed|{e_rule}",
            "aa_kundennummer_hash": None, "offer_kind": "AUSBILDUNG" if role == "ausbildung" else "ARBEIT", "hauptberuf": None, "alle_berufe": [],
            "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, ""), "department_hint": department_hint(f"{title} {dept or ''}"),
            "department_raw": dept, "city": city, "plz": plz, "region": region, "lat": None, "lon": None, "in_bavaria": in_bavaria(city, plz, region, self.towns),
            "n_locations": 1, "locations": json.dumps([{"adresse": {"ort": city, "plz": plz, "region": region}}], ensure_ascii=False),
            "employment_types": [], "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None, "contract": None, "fixed_term_months": None,
            "start_date": None, "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
            "first_published": published, "last_modified": None, "valid_until": valid, "external_url": url, "description": (desc or "")[:20000] or None,
            **enr, "details_fetched_at": datetime.now(timezone.utc).isoformat() if desc else None, "details_error": None,
            "fuzzy_key": fuzzy_key(title, emp, city), "content_hash": content_hash(title, emp, city, desc[:200] if desc else None),
            "payload": json.dumps({"crawl": {"seed": seed["career"], "kez": seed.get("kez"), "parse": parse}}, ensure_ascii=False),
            # A shared-hub seed (seed.get("operator") set, see _base() in ats_seeds.py) genuinely
            # covers several distinct real sites -- defaulting an unmatched row to the ONE seed
            # clinic that happened to kick off the crawl is exactly the wrong-guess bug this operator
            # fix exists to remove (confirmed live 2026-09-11: a Klinikverbund Allgäu posting in
            # Memmingen, a town with no registered clinic in the group, silently landed on
            # Immenstadt). Single-site seeds keep the old default -- there is no other candidate to
            # confuse it with.
            "_kez": None if seed.get("operator") else seed.get("kez"),
            # True only when emp fell all the way through to the bare seed clinic's own registry
            # name (no page-stated employer, no safer shared operator name) -- consumed by
            # app/crawl.py _load_observations -> registry.Matcher.match(employer_inherited=...),
            # which then skips the circular R1_exact/R2_operator match against the name the crawler
            # itself copied from the seed clinic on a board that may cover several distinct sites.
            # An operator-derived name is NOT marked inherited: R2_operator(_town) already restricts
            # a multi-site operator match to the posting's own town when more than one registry
            # clinic shares that operator, which is the deliberately safe path (see ats_seeds.py
            # umantis()'s hub_needs_own_host case).
            "_emp_inherited": employer is None and not seed.get("operator"),
        }

    def _from_jsonld(self, jp, url, seed, section_confirmed=False):
        locs = _location(jp)
        l = next((x for x in locs if in_bavaria(x["city"], x["plz"], x["region"], self.towns)), locs[0] if locs else {"city": None, "plz": None, "region": None})
        if not l.get("city") and not l.get("plz") and seed.get("town"):        # JSON-LD without address (d.vinci easy): seed town
            l = {"city": seed["town"], "plz": None, "region": None}
        if seed.get("town") is None:                                            # multi-site operator: a town named in the title wins over an HQ address
            tm = re.search(r"\b(?:in|am|im|Standort|Klinikum|Klinik|Krankenhaus)\s+(?:kbo-)?([A-ZÄÖÜ][\wäöüß\-]+(?: (?:am|an der|im|bei|in der) [A-ZÄÖÜ][\wäöüß\-]+)?)", _strip(jp.get("title") or ""))
            if tm and norm_text(tm.group(1)).split()[0] in self.towns and norm_text(tm.group(1)) != norm_text(l.get("city") or ""):
                l = {"city": tm.group(1), "plz": None, "region": None}
        desc = _strip(jp.get("description") or "")
        et = jp.get("employmentType"); et = " ".join(et) if isinstance(et, list) else (et or "")
        ho = jp.get("hiringOrganization"); ho_name = (ho.get("name") if isinstance(ho, dict) else ho) if ho else None
        ho_name = _strip(ho_name) if isinstance(ho_name, str) and len(ho_name) > 3 else None
        # Always trust the posting's own hiringOrganization when the page states one -- it used to be
        # discarded whenever seed["town"] was set (true for every registry-built seed, i.e. almost
        # always), which is exactly how a shared softgarden/umantis board ended up stamping every
        # posting with the seed clinic's own name regardless of which site the posting itself names
        # (confirmed live 2026-09-18: Starnberger/Heiligenfeld/Passauer Wolf sibling-site postings).
        # _base() marks the row employer-inherited (skipping Matcher's R1/R2) whenever ho_name is
        # None and there is no safer operator fallback either.
        o = self._base(url, seed, _strip(jp.get("title") or ""), desc, l["city"], l["plz"], l["region"],
                       (jp.get("datePosted") or "")[:10] or None, (jp.get("validThrough") or "")[:10] or None, None, "jsonld",
                       employer=ho_name, section_confirmed=section_confirmed)
        o["employment_types"] = [t for t, k in (("vollzeit", "FULL_TIME"), ("teilzeit", "PART_TIME"), ("minijob", "MINI")) if k in et.upper()]
        if re.search(r"TEMPORARY|BEFRISTET", et, re.I): o["contract"] = "BEFRISTET"
        return o

    def _heuristic(self, html, url, seed, anchor=None, section_confirmed=False):
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        title = _strip(m.group(1)) if m else ""
        if not title or (anchor and not JOB_TEXT.search(title)):
            title = anchor or title or _strip(re.search(r"<title>(.*?)</title>", html, re.S | re.I).group(1) if re.search(r"<title>", html, re.I) else "")
        if not re.search(r"bewerb|apply", html, re.I): return None
        title = re.sub(r"\s*[|–-]\s*(Karriere|Jobs|Stellenangebote).*$", "", title)[:200]
        if not title or re.fullmatch(r"[\w\s\-/&,\.]+\(\d+\)", title.strip()): return None     # category links like "Pflegedienst (5)"
        txt = _strip(html)
        city = seed.get("town"); plz = None
        # 1) town named in the title ("am BKH Passau", "in Freising", "Standort Landau"), 2) explicit Einsatzort/Arbeitsort label, 3) seed town
        found_city = False
        tm = re.search(r"(?:\bin|\bam|\bim|\bfür|Standort|Klinik(?:um)?)\s+(?:BKH|Klinikum|Klinik|Krankenhaus)?\s*([A-ZÄÖÜ][\wäöüß\-]+(?: (?:am|an der|im|bei|in der) [A-ZÄÖÜ][\wäöüß\-]+)?)", title)
        if tm and norm_text(tm.group(1)).split()[0] in self.towns and norm_text(tm.group(1)) not in ("bayern",):
            city, found_city = tm.group(1), True
        else:
            m = re.search(r"(?:Einsatzort|Arbeitsort|Dienstort)\s*[:\-]?\s*([A-ZÄÖÜ][\wäöüß\-\.]+(?: (?:am|an der|im|bei|in der) [A-ZÄÖÜ][\wäöüß\-]+)?)", txt)
            if m and norm_text(m.group(1)).split()[0] in self.towns:
                city, found_city = m.group(1), True
        if not found_city:
            # A bare "12345 Ort" pair found anywhere on the page is regularly a contact/imprint/
            # letterhead address, not the posting's own site (confirmed live: the Medic-Center
            # Fürth board relabelled its own town "Nürnberg" from an "Ihr Ansprechpartner" contact
            # block) -- only fall back to it when neither the title nor an Einsatzort/Arbeitsort
            # label named anything, matching pflege_jobs.verify's TRUSTED_LOC discipline (a bare
            # plz_ort pair is confirming evidence only, never primary).
            m = re.search(r"\b(63[7-9]\d\d|8\d{4}|9[0-7]\d{3})\s+([A-ZÄÖÜ][a-zäöüß\-]+)", txt)
            if m and norm_text(m.group(2)) in self.towns: plz, city = m.group(1), m.group(2)
        # No JSON-LD here, but the source text often still states these directly (e.g. umantis detail
        # pages: "Veröffentlichung ab 28.07.2026", "in Vollzeit (38,5 Std./Woche)") -- pick them up
        # rather than leaving fields empty the source actually exposes, same fields _from_jsonld reads.
        dm = re.search(r"Ver[öo]ffentlichung\s*(?:ab)?\s*[:\-]?\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", txt, re.I)
        published = "%s-%02d-%02d" % (dm.group(3), int(dm.group(2)), int(dm.group(1))) if dm else None
        o = self._base(url, seed, title, txt[:20000], city, plz, None, published, None, None, "heuristic", section_confirmed=section_confirmed)
        o["employment_types"] = [t for t, kw in (("vollzeit", "Vollzeit"), ("teilzeit", "Teilzeit"), ("minijob", "Minijob")) if re.search(r"\b" + kw + r"\b", txt, re.I)]
        return o


def crawl_all(seeds, towns, budget=120, log=print):
    cr = Crawler(towns, per_site_pages=budget, log=log)
    all_rows, report = [], []
    for s in seeds:
        t = time.time()
        rows, st = cr.crawl(s)
        st.update({"name": s["name"], "found": len(rows), "secs": round(time.time() - t)})
        report.append(st); all_rows += rows
        log(f"{s['name'][:36]:<36} lists {st['list_pages']:>2} links {st['job_links_found']:>3} fetched {st['job_pages']:>3} jsonld {st['jobposting_pages']:>3} heur {st['heuristic_pages']:>3} -> {len(rows):>3} rows {st['secs']}s")
    return all_rows, report
