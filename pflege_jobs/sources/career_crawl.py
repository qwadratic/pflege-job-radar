"""Career-portal crawler (source employer_ats, precedence 2).

Per clinic seed: BFS within the career host(s), depth <= 2, page budget; every fetched page is checked for
schema.org JobPosting JSON-LD (the de-facto standard emitted by softgarden, d.vinci, rexx, concludis, SmartRecruiters,
Personio, mein-check-in...). Pages without JSON-LD but with a title + "Bewerben" are parsed heuristically
(title = <h1>/<title>, location from text) and flagged `parse='heuristic'`.
Bavaria detection: jobLocation.addressRegion in {Bayern, Bavaria} OR postal code in Bavarian ranges OR city in the
Bavarian town list (registry + Arbeitsagentur) -- written onto each row as in_bavaria, not used to drop rows here.
robots.txt is honoured (urllib.robotparser); throttle per host.
"""
import json
import re
import time
import urllib.robotparser
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import requests

from .. import config as C
from ..classify import (classify_employer, classify_role, content_hash, department_hint, employer_norm,
                        enrich_description, fuzzy_key, norm_text, qualification_hint)
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
BAV_PLZ = re.compile(r"^(63[7-9]\d\d|8[0-7]\d{3}|881[3-7]\d|89[2-5]\d\d|9[0-7]\d{3})$")
NON_BAV_CITIES = {"frankfurt", "frankfurt (oder)", "gießen", "marburg", "bad berka", "leipzig", "berlin", "hamburg", "stuttgart", "ulm", "köln",
                  "düsseldorf", "hannover", "dresden", "erfurt", "kassel", "wiesbaden", "mainz", "heidelberg", "mannheim", "karlsruhe", "freiburg"}


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
    txt = re.sub(r"&nbsp;", " ", txt); txt = re.sub(r"&amp;", "&", txt); txt = re.sub(r"&#?\w+;", " ", txt)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", txt)).strip()


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


def in_bavaria(city, plz, region, towns):
    # A malformed upstream row can carry a one-item list instead of a scalar (seen live 2026-09-09,
    # inbox_id 13576: {"plz": ["97318"], "city": ["Kitzingen"]}) -- one bad row must never crash the
    # whole drain for every other pending row behind it.
    if isinstance(plz, list): plz = plz[0] if plz else None
    if isinstance(city, list): city = city[0] if city else None
    if isinstance(region, list): region = region[0] if region else None
    if region and re.search(r"bayern|bavaria|^by$", str(region).strip(), re.I): return True
    if region and re.search(r"hessen|thüringen|sachsen|brandenburg|baden|württemberg|nordrhein|niedersachsen|berlin|hamburg|rheinland|saarland|schleswig|mecklenburg|bremen|^(nw|he|bw|th|sn|ni|rp|sh|mv|bb|hh|hb|be|sl|st)$", str(region).strip(), re.I): return False
    if plz and BAV_PLZ.match(plz): return True
    if plz and re.match(r"^\d{5}$", plz): return False
    c = norm_text(city or "")
    if not c: return None
    if c in NON_BAV_CITIES: return False
    if c.split(",")[0].strip() in towns: return True
    first = c.split()[0]
    if first not in GENERIC_PREFIX and first in towns: return True
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

    def sitemap_job_urls(self, url, depth=0, limit=400):
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
            out = []
            for l in locs[:10]:
                if re.search(r"job|stelle|karriere|vacanc|post", l, re.I) or len(locs) <= 4: out += self.sitemap_job_urls(l, 1, limit)
            return out[:limit]
        return [l for l in locs if JOB_HREF.search(l) or re.search(r"/(job|stelle|vacanc|karriere/[^/]+/[^/]+)", l, re.I)][:limit]

    def _page_hosts_ok(self, u, hosts):
        p = urlparse(u)
        if p.scheme not in ("http", "https") or LINK_BAD.search(u): return False
        return p.netloc in hosts or any(p.netloc.endswith("." + h.split(".", 1)[-1]) and ("job" in p.netloc or "karriere" in p.netloc or "softgarden" in p.netloc or "dvinci" in p.netloc) for h in hosts)

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
        seen_lists, job_links = set(), {}
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
            for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
                href, inner = m.group(1), _strip(m.group(2))[:200]
                u = urldefrag(urljoin(r.url, href))[0]
                if not self._page_hosts_ok(u, hosts): continue
                if u in job_links or u in seen_lists: continue
                is_job = bool(JOB_TEXT.search(inner)) or (bool(JOB_HREF.search(u)) and inner and not LIST_NAV.fullmatch(inner.strip()))
                if is_job and inner and len(inner) > 6 and not re.search(r"^(mehr|details?|zur stelle|jetzt bewerben|weiterlesen|ansehen)$", inner.strip(), re.I) or (is_job and JOB_HREF.search(u) and not inner):
                    job_links[u] = inner
                elif (PAGINATE.search(u) or LIST_NAV.search(inner) or LIST_NAV.search(u)) and len(seen_lists) + len(list_q) < self.list_budget * 2:
                    if depth_cap is None or depth < depth_cap:
                        list_q.append((u, depth + 1))
        for sm in sitemaps:
            for u in self.sitemap_job_urls(sm):
                if u not in job_links and not LINK_BAD.search(u): job_links[u] = ""
        stats["sitemap_links"] = sum(1 for v in job_links.values() if v == "")
        jobs = {}
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
        if section_rows:
            seen_urls = {r["external_url"] for r in rows}
            rows = rows + [r for r in section_rows if r["external_url"] not in seen_urls]
            for k in ("list_pages", "job_pages", "jobposting_pages", "heuristic_pages"):
                stats[k] = stats.get(k, 0) + section_stats.get(k, 0)
        stats["section_first"] = bool(section_href)
        return rows, stats

    def _base(self, url, seed, title, desc, city, plz, region, published, valid, dept, parse, employer=None, section_confirmed=False):
        emp = employer or seed["name"]
        e_class, e_rule = classify_employer(emp)
        role, rule = classify_role(title, "", nursing_section_confirmed=section_confirmed)
        enr = {("enr_" + k): v for k, v in enrich_description(desc or "").items()}
        return {
            "source_id": SOURCE_ID, "source_ref": url, "source_url": url, "observed_at": datetime.now(timezone.utc).isoformat(),
            "title": title, "employer_name": emp, "employer_name_norm": employer_norm(emp),
            "employer_class": "clinic" if e_class != "clinic" else e_class, "employer_class_rule": e_rule if e_class == "clinic" else f"registry_seed|{e_rule}",
            "aa_kundennummer_hash": None, "offer_kind": "AUSBILDUNG" if role == "ausbildung" else "ARBEIT", "hauptberuf": None, "alle_berufe": [],
            "role_class": role, "role_rule": rule, "qualification_hint": qualification_hint(title, ""), "department_hint": department_hint(f"{title} {dept or ''}"),
            "department_raw": dept, "city": city, "plz": plz, "region": "BAYERN", "lat": None, "lon": None, "in_bavaria": in_bavaria(city, plz, region, self.towns),
            "n_locations": 1, "locations": json.dumps([{"adresse": {"ort": city, "plz": plz, "region": region}}], ensure_ascii=False),
            "employment_types": [], "shift_night_weekend": None, "homeoffice": None, "quereinstieg": None, "contract": None, "fixed_term_months": None,
            "start_date": None, "salary_min": None, "salary_max": None, "salary_unit": None, "salary_note": None,
            "first_published": published, "last_modified": None, "valid_until": valid, "external_url": url, "description": (desc or "")[:20000] or None,
            **enr, "details_fetched_at": datetime.now(timezone.utc).isoformat() if desc else None, "details_error": None,
            "fuzzy_key": fuzzy_key(title, emp, city), "content_hash": content_hash(title, emp, city, desc[:200] if desc else None),
            "payload": json.dumps({"crawl": {"seed": seed["career"], "kez": seed.get("kez"), "parse": parse}}, ensure_ascii=False),
            "_kez": seed.get("kez"),
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
        o = self._base(url, seed, _strip(jp.get("title") or ""), desc, l["city"], l["plz"], l["region"],
                       (jp.get("datePosted") or "")[:10] or None, (jp.get("validThrough") or "")[:10] or None, None, "jsonld",
                       employer=ho_name if seed.get("town") is None else None,   # multi-site seeds: trust the posting's organisation
                       section_confirmed=section_confirmed)
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
        tm = re.search(r"(?:\bin|\bam|\bim|\bfür|Standort|Klinik(?:um)?)\s+(?:BKH|Klinikum|Klinik|Krankenhaus)?\s*([A-ZÄÖÜ][\wäöüß\-]+(?: (?:am|an der|im|bei|in der) [A-ZÄÖÜ][\wäöüß\-]+)?)", title)
        if tm and norm_text(tm.group(1)).split()[0] in self.towns and norm_text(tm.group(1)) not in ("bayern",): city = tm.group(1)
        else:
            m = re.search(r"(?:Einsatzort|Arbeitsort|Dienstort)\s*[:\-]?\s*([A-ZÄÖÜ][\wäöüß\-\.]+(?: (?:am|an der|im|bei|in der) [A-ZÄÖÜ][\wäöüß\-]+)?)", txt)
            if m and norm_text(m.group(1)).split()[0] in self.towns: city = m.group(1)
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
