"""Career-portal crawler (source employer_ats, precedence 2).

Per clinic seed: BFS within the career host(s), depth <= 2, page budget; every fetched page is checked for
schema.org JobPosting JSON-LD (the de-facto standard emitted by softgarden, d.vinci, rexx, concludis, SmartRecruiters,
Personio, mein-check-in...). Pages without JSON-LD but with a title + "Bewerben" are parsed heuristically
(title = <h1>/<title>, location from text) and flagged `parse='heuristic'`.
Bavaria filter: jobLocation.addressRegion in {Bayern, Bavaria} OR postal code in Bavarian ranges OR city in the
Bavarian town list (registry + Arbeitsagentur). Non-Bavarian sites of chains are dropped (counted in stats).
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

SOURCE_ID = C.SOURCES["employer_ats"]["source_id"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 pflege-jobs-crawler"
LINK_OK = re.compile(r"job|stelle|vacanc|position|karriere|career|bewerb|angebot|offer|posting|/p/|/de/", re.I)
JOB_HREF = re.compile(r"/detail/|/detailansicht/|/job/|/jobad\?|/jobs?/[^/?]*\d|/karriere/jobs/|/stellenangebot|/stellenanzeige|/vacanc|/position/|jobid|job_id|jobdetail|/p/|[?&]id=\d|/de/jobs/\d|/jobs/\d", re.I)
JOB_TEXT = re.compile(r"\((?:m|w|d|x|i|gn|a)\s?[/|\\*]\s?(?:m|w|d|x|i|gn|a)(?:\s?[/|\\*]\s?(?:m|w|d|x|i|gn|a))?\)|\b[mwd]/[mwd]/[mwdx]\b|\*in\b|:in\b", re.I)
LIST_NAV = re.compile(r"weiter|nächste|next|mehr laden|alle stellen|page|seite|pflege|krankenpflege|medizin|berufsgruppe|fachbereich|kategorie|filter", re.I)
LINK_BAD = re.compile(r"\.(pdf|jpe?g|png|gif|svg|css|js|zip|docx?|xlsx?)(\?|$)|mailto:|tel:|javascript:|#|login|logout|datenschutz|impressum|agb|cookie|newsletter|facebook|instagram|linkedin|xing|youtube|twitter|share|print", re.I)
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
    def __init__(self, towns, per_site_pages=120, list_pages=12, workers=1, sleep=0.25, log=print):
        self.towns, self.budget, self.list_budget, self.sleep, self.log = towns, per_site_pages, list_pages, sleep, log
        self.s = requests.Session(); self.s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
        self.robots = {}

    def allowed(self, url):
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try: rp.set_url(host + "/robots.txt"); rp.read()
            except Exception: rp = None
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

    def crawl(self, seed):
        """Listing-first: seed + extra seeds + pagination/filter pages -> job links (title-like anchor text or job-like href)
        -> fetch detail pages -> JSON-LD JobPosting or heuristic. seed: {name, kez, career, hosts?, extra_seeds?, bavaria_only_operator?}"""
        hosts = set(seed.get("hosts") or []) | {urlparse(seed["career"]).netloc}
        list_q = deque([seed["career"]] + list(seed.get("extra_seeds", [])))
        seen_lists, job_links, stats = set(), {}, {"list_pages": 0, "job_pages": 0, "jobposting_pages": 0, "heuristic_pages": 0, "dropped_non_bavaria": 0, "dropped_unknown_loc": 0, "dropped_not_pflege": 0}
        while list_q and stats["list_pages"] < self.list_budget:
            url = urldefrag(list_q.popleft())[0]
            if url in seen_lists: continue
            seen_lists.add(url)
            r = self.fetch(url)
            if not r: continue
            stats["list_pages"] += 1
            html = r.text
            for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
                href, inner = m.group(1), _strip(m.group(2))[:200]
                u = urldefrag(urljoin(r.url, href))[0]
                p = urlparse(u)
                if p.scheme not in ("http", "https") or LINK_BAD.search(u): continue
                same_host = p.netloc in hosts or any(p.netloc.endswith("." + h.split(".", 1)[-1]) and ("job" in p.netloc or "karriere" in p.netloc or "softgarden" in p.netloc or "dvinci" in p.netloc) for h in hosts)
                if not same_host: continue
                if u in job_links or u in seen_lists: continue
                is_job = bool(JOB_TEXT.search(inner)) or (bool(JOB_HREF.search(u)) and inner and not LIST_NAV.fullmatch(inner.strip()))
                if is_job and inner and len(inner) > 6 and not re.search(r"^(mehr|details?|zur stelle|jetzt bewerben|weiterlesen|ansehen)$", inner.strip(), re.I) or (is_job and JOB_HREF.search(u) and not inner):
                    job_links[u] = inner
                elif (PAGINATE.search(u) or LIST_NAV.search(inner) or LIST_NAV.search(u)) and len(seen_lists) + len(list_q) < self.list_budget * 2:
                    list_q.append(u)
        for sm in seed.get("sitemaps", []):
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
                jobs[r.url] = self._from_jsonld(jps[0], r.url, seed)
            else:
                h = self._heuristic(r.text, r.url, seed, anchor)
                if h: jobs[r.url] = h; stats["heuristic_pages"] += 1
        out = []
        for j in jobs.values():
            if j["role_class"] == "nicht_pflege": stats["dropped_not_pflege"] += 1; continue
            if j["in_bavaria"] is False: stats["dropped_non_bavaria"] += 1; continue
            if j["in_bavaria"] is None and seed.get("bavaria_only_operator"): j["in_bavaria"] = True
            if j["in_bavaria"] is None: stats["dropped_unknown_loc"] += 1; continue
            out.append(j)
        stats["job_links_found"] = len(job_links)
        return out, stats

    def _base(self, url, seed, title, desc, city, plz, region, published, valid, dept, parse, employer=None):
        emp = employer or seed["name"]
        e_class, e_rule = classify_employer(emp)
        role, rule = classify_role(title, "")
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

    def _from_jsonld(self, jp, url, seed):
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
                       employer=ho_name if seed.get("town") is None else None)   # multi-site seeds: trust the posting's organisation
        o["employment_types"] = [t for t, k in (("vollzeit", "FULL_TIME"), ("teilzeit", "PART_TIME"), ("minijob", "MINI")) if k in et.upper()]
        if re.search(r"TEMPORARY|BEFRISTET", et, re.I): o["contract"] = "BEFRISTET"
        return o

    def _heuristic(self, html, url, seed, anchor=None):
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
        return self._base(url, seed, title, txt[:20000], city, plz, None, None, None, None, "heuristic")


def crawl_all(seeds, towns, budget=120, log=print):
    cr = Crawler(towns, per_site_pages=budget, log=log)
    all_rows, report = [], []
    for s in seeds:
        t = time.time()
        rows, st = cr.crawl(s)
        st.update({"name": s["name"], "found": len(rows), "secs": round(time.time() - t)})
        report.append(st); all_rows += rows
        log(f"{s['name'][:36]:<36} lists {st['list_pages']:>2} links {st['job_links_found']:>3} fetched {st['job_pages']:>3} jsonld {st['jobposting_pages']:>3} heur {st['heuristic_pages']:>3} -> {len(rows):>3} Pflege/BY (drop nonBY {st['dropped_non_bavaria']}, noloc {st['dropped_unknown_loc']}, nonpflege {st['dropped_not_pflege']}) {st['secs']}s")
    return all_rows, report
