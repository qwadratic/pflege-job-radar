"""Playwright-based egress crawler for walled portals (StepStone, etc.).

Replaces the Anthropic web_fetch approach with headless Chromium.
Results are written to local JSONL files in crawl_output/.
Use crawlers/load_crawl_output.py to push results into Supabase inbox.

  python crawlers/claude_egress.py stepstone-matrix "Augsburg|Regensburg" "pflegefachkraft|krankenpfleger"   # subset smoke test
  python crawlers/claude_egress.py stepstone-matrix               # full: 40 cities x 12 keywords
  python crawlers/claude_egress.py stepstone 1-3                  # pages 1-3 of one listing
  python crawlers/claude_egress.py helios-detail <job-url> ...    # helios detail pages (JSON-LD)
  python crawlers/claude_egress.py url <any-url>                  # generic: JSON-LD + (m/w/d) links
"""
import json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

import requests

OUTPUT_DIR = Path(os.environ.get("EGRESS_OUTPUT_DIR", "crawl_output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

INBOX = os.environ.get("SUPABASE_URL", "https://klkxfvieaxpjlplloljn.supabase.co") + "/rest/v1/inbox"
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
CID = "playwright-egress-" + os.environ.get("EGRESS_CLIENT", "default")
GM = re.compile(r"\((?:m|w|d|x|i)\s?/\s?(?:m|w|d|x|i)(?:\s?/\s?(?:m|w|d|x|i))?\)", re.I)

# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------
_browser = None
_pw = None


def _get_browser():
    global _browser, _pw
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(headless=True, args=["--no-sandbox"])
    return _browser


def _close_browser():
    global _browser, _pw
    if _browser:
        try: _browser.close()
        except Exception: pass
        _browser = None
    if _pw:
        try: _pw.stop()
        except Exception: pass
        _pw = None


def fetch_page(url: str, wait_ms: int = 5000, timeout_ms: int = 30000):
    """Render a page with Playwright. Returns (page, ctx) — caller must close both."""
    b = _get_browser()
    ctx = b.new_context(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        locale="de-DE", viewport={"width": 1280, "height": 2000},
    )
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        page.close(); ctx.close(); raise
    page.wait_for_timeout(wait_ms)
    return page, ctx


# ---------------------------------------------------------------------------
# StepStone listing parser (Playwright DOM)
# ---------------------------------------------------------------------------
def parse_stepstone_pw(page, page_url: str):
    """Extract job cards from a rendered StepStone listing page via data-at attributes."""
    cards = page.evaluate(r'''() => {
        const titles = document.querySelectorAll('[data-at="job-item-title"]');
        return Array.from(titles).map(a => {
            const card = a.closest('[data-at="job-item"]') || a.closest('article') || a.parentElement?.parentElement?.parentElement;
            const emp = card ? (card.querySelector('[data-at="job-item-company-name"]') || {}).textContent : null;
            const loc = card ? (card.querySelector('[data-at="job-item-location"]') || {}).textContent : null;
            return {
                title: a.textContent.trim(),
                url: a.href,
                org: emp ? emp.trim() : null,
                city: loc ? loc.trim() : null
            };
        });
    }''')
    out = []
    for c in cards:
        if not c.get("url") or "stellenangebote--" not in c["url"]:
            continue
        out.append({
            "title": c["title"],
            "org": c.get("org") or "",
            "loc": [{"city": (c.get("city") or "").split(",")[0], "plz": None, "region": None}],
            "url": c["url"], "page": page_url, "description": None,
        })
    return out


def parse_external_links_pw(page):
    """Extract external (non-StepStone) links from a rendered page."""
    return page.evaluate(r'''() => {
        return Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(u => u.startsWith('http') && !u.includes('stepstone.de'));
    }''')


def parse_jsonld_pw(page):
    """Extract JSON-LD script contents from a rendered page."""
    return page.evaluate(r'''() => {
        return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
            .map(s => s.textContent);
    }''')


def parse_links_as_md(page):
    """Extract all links as markdown-formatted text for generic link parsing."""
    return page.evaluate(r'''() => {
        return Array.from(document.querySelectorAll('a[href]'))
            .map(a => '[' + a.textContent.trim() + '](' + a.href + ')')
            .join('\n');
    }''')


# ---------------------------------------------------------------------------
# Markdown-based parsers (kept for test compat + exa_egress imports)
# ---------------------------------------------------------------------------
def parse_stepstone(md: str, page_url: str):
    """Cards: '[Employer](cmp-url)' then '## [Title](job-url)' then employer, city."""
    out = []
    for m in re.finditer(
        r"## \[(?P<title>[^\]]+)\]\((?P<url>https://www\.stepstone\.de/stellenangebote--[^)]+)\)\s*\n\s*(?P<emp>[^\n]+)\n\s*(?P<city>[^\n]+)", md
    ):
        out.append({"title": m["title"].strip(), "org": m["emp"].strip(),
                    "loc": [{"city": m["city"].strip().split(",")[0], "plz": None, "region": None}],
                    "url": m["url"], "page": page_url, "description": None})
    return out


def parse_stepstone_text(md: str, page_url: str):
    """Exa-style markdown without links."""
    out, body = [], md.split("Treffer für", 1)[-1]
    body = body.split("Diese Jobs waren bei anderen Jobsuchenden beliebt")[0]
    for m in re.finditer(r"\n## (?P<title>[^\n]{4,200})\n+(?P<emp>[^\n#]{2,120})\n+(?P<city>[^\n#]{2,160})\n", body):
        t, e, c = m["title"].strip(), m["emp"].strip(), m["city"].strip()
        if re.search(r"^(Gehalt anzeigen|Schnelle Bewerbung|Anschreiben|Teilweise|vor \d)", e) or re.search(r"^(Gehalt anzeigen|Schnelle Bewerbung|Anschreiben)", c):
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", (t + "-" + e + "-" + c).lower()).strip("-")[:120]
        age = re.search(r"vor (\d+) (Stunden?|Tag(?:en)?|Wochen?|Monat(?:en)?)", body[m.end():m.end() + 900])
        out.append({"title": t, "org": e, "loc": [{"city": c.split(",")[0].strip(), "plz": (re.match(r"(\d{5}) ", c) or [None, None])[1], "region": None}],
                    "url": f"{page_url}#{slug}", "page": page_url, "description": None, "age": age.group(0) if age else None, "listing_only": True})
    return out


def parse_stepstone_detail(md: str, url: str):
    """Exa-style detail page."""
    t = re.search(r"\n# (?P<title>[^\n]{4,200})\n", md)
    meta = re.findall(r"\n- ([^\n]{2,120})", md[:4000])
    if not t or len(meta) < 2:
        return None
    emp, city = meta[0].strip(), meta[1].strip()
    et = [x for x in ("Vollzeit", "Teilzeit") if any(x in m for m in meta[:6])]
    desc = md.split("#### Diese Jobs waren")[0]
    return {"title": t["title"].strip(), "org": emp,
            "loc": [{"city": city.split(",")[0], "plz": None, "region": None}],
            "url": url, "page": url, "employmentType": ",".join(et) or None,
            "description": re.sub(r"\s+", " ", desc)[:20000]}


def parse_jsonld_lines(md: str, page_url: str):
    out = []
    for m in re.finditer(r'\{"hiringOrganization".*?"title":"(?P<t>[^"]+)".*?\}', md, re.S):
        try: j = json.loads(m.group(0))
        except Exception: continue
        a = (j.get("jobLocation") or {}).get("address") or {}
        out.append({"title": j.get("title"), "org": (j.get("hiringOrganization") or {}).get("name"),
                    "datePosted": j.get("datePosted"), "employmentType": j.get("employmentType"),
                    "loc": [{"city": a.get("addressLocality"), "plz": a.get("postalCode"), "region": a.get("addressRegion")}],
                    "url": page_url, "description": (j.get("description") or "")[:20000], "page": page_url})
    return out


def parse_generic_links(md: str, page_url: str):
    return [{"t": m["t"].strip(), "h": m["h"]} for m in re.finditer(r"\[(?P<t>[^\]]{6,200})\]\((?P<h>https?://[^)]+)\)", md) if GM.search(m["t"])]


# ---------------------------------------------------------------------------
# ATS discovery
# ---------------------------------------------------------------------------
ATS_HOSTS = [
    ("softgarden", r"softgarden\.io|jobdb\.softgarden"), ("smartrecruiters", r"smartrecruiters\.com"),
    ("bite", r"jobs\.b-ite\.com|/jobposting/[0-9a-f]{40}"), ("pi_asp", r"pi-asp\.de"),
    ("dvinci", r"dvinci"), ("rexx", r"rexx-systems|/stellenangebot\.html"),
    ("umantis", r"umantis\.com"), ("concludis", r"concludis"), ("talention", r"talention"),
    ("personio", r"personio\.(de|com)"), ("mein-check-in", r"mein-check-in\.de"),
    ("helix", r"helixjobs\.com"), ("oracle", r"oraclecloud\.com"), ("workday", r"myworkday"),
    ("successfactors", r"successfactors|jobs2web"), ("onlyfy", r"onlyfy|prescreen"),
    ("interamt", r"interamt"), ("mhm", r"mhm-hr\.com"), ("recruitee", r"recruitee\.com"),
    ("join", r"join\.com"), ("heyjobs", r"heyjobs\.co"),
]


def parse_external_links(md: str):
    return [u for u in re.findall(r"\((https?://[^)\s]+)\)", md) if "stepstone" not in u.lower()]


def fingerprint_ats(urls):
    for u in urls:
        for name, rx in ATS_HOSTS:
            if re.search(rx, u, re.I): return name, u
    return None, None


def discover_ats(cards, max_n=40):
    import csv
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pflege_jobs.registry import Matcher
    clinics_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data/registry/clinics.csv")
    clinics = list(csv.DictReader(open(clinics_path, encoding="utf-8")))
    for c in clinics: c["beds"] = int(c["beds"]) if c.get("beds") else None
    m = Matcher([dict(c) for c in clinics]); byid = {c["clinic_id"]: c for c in clinics}
    probes, seen, n = [], set(), 0
    for card in cards:
        mt = m.match(card.get("org") or "", (card.get("loc") or [{}])[0].get("city"))
        if not mt or mt[0] in seen: continue
        c = byid[mt[0]]
        if c.get("ats_type"): continue
        if n >= max_n: break
        seen.add(mt[0]); n += 1
        try:
            page, ctx = fetch_page(card["url"], wait_ms=4000)
            links = parse_external_links_pw(page)
            page.close(); ctx.close()
        except Exception as e:
            print(f"  discover fetch failed: {e}"); continue
        ats, apply_url = fingerprint_ats(links)
        own = next((u for u in links if re.search(r"karriere|stellen|jobs|bewerb", u, re.I)), None)
        probes.append({"kind": "probe", "source_host": "www.stepstone.de", "source_url": card["url"],
                       "collector": "playwright-egress-ats-discovery-v1", "client_id": CID,
                       "payload": {"probe": "ats_discovery", "employer": card.get("org"), "clinic_id": mt[0],
                                   "clinic_name": c["name"], "ats": ats, "apply_url": apply_url, "careers_url": own,
                                   "external_links": links[:20]}})
        print(f"  discover {c['name'][:40]:<40} -> {ats or '?'} {apply_url or own or ''}")
    return probes


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def save_rows(rows, tag: str):
    """Append rows to a JSONL file in OUTPUT_DIR."""
    if not rows: return 0
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = OUTPUT_DIR / f"{tag}_{ts}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def post_inbox(rows):
    """POST rows to Supabase inbox (used by exa_egress). Falls back to local save if no ANON key."""
    if not rows: return 0
    if not ANON:
        return save_rows(rows, "inbox")
    r = requests.post(INBOX, headers={"apikey": ANON, "Authorization": f"Bearer {ANON}",
                      "Content-Profile": "pflege_jobs", "Content-Type": "application/json",
                      "Prefer": "return=minimal"}, json=rows, timeout=60)
    r.raise_for_status(); return len(rows)


# ---------------------------------------------------------------------------
# Matrix config
# ---------------------------------------------------------------------------
KEYWORDS = ["pflegefachkraft", "krankenpfleger", "pflegekraft", "krankenschwester-pfleger",
            "examinierte-pflegefachkraft", "intensivpflege", "pflegedienstleitung",
            "stationsleitung", "praxisanleiter", "ota", "hebamme", "pflegehelfer"]
CITIES = ["München", "Nürnberg", "Augsburg", "Würzburg", "Regensburg", "Ingolstadt",
          "Fürth", "Erlangen", "Bayreuth", "Bamberg", "Landshut", "Aschaffenburg",
          "Kempten", "Rosenheim", "Schweinfurt", "Passau", "Neu-Ulm", "Hof",
          "Weiden in der Oberpfalz", "Amberg", "Coburg", "Ansbach", "Straubing",
          "Deggendorf", "Dachau", "Freising", "Erding", "Fürstenfeldbruck",
          "Starnberg", "Traunstein", "Bad Kissingen", "Kaufbeuren", "Memmingen",
          "Landsberg am Lech", "Günzburg", "Neuburg an der Donau",
          "Weilheim in Oberbayern", "Garmisch-Partenkirchen", "Forchheim", "Kulmbach"]


def city_slug(c):
    from urllib.parse import quote
    return "in-" + quote(c.lower().replace(" ", "-"), safe="-")


def stepstone_matrix_urls(cities=None, keywords=None):
    return [f"https://www.stepstone.de/jobs/{k}/{city_slug(c)}"
            for c in (cities or CITIES) for k in (keywords or KEYWORDS)]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv):
    mode, args = argv[0], argv[1:]
    total = 0
    do_discover = os.environ.get("EGRESS_DISCOVER", "1") != "0"
    discover_max = int(os.environ.get("EGRESS_DISCOVER_MAX", "40"))

    try:
        if mode == "stepstone":
            pages_arg = args[0] if args else "1-5"
            a, b = (pages_arg.split("-") + [pages_arg])[:2]
            base = os.environ.get("STEPSTONE_URL", "https://www.stepstone.de/jobs/pflegefachkraft/in-bayern")
            for p_num in range(int(a), int(b) + 1):
                url = f"{base}?page={p_num}"
                try:
                    page, ctx = fetch_page(url)
                    jobs = parse_stepstone_pw(page, url)
                    page.close(); ctx.close()
                except Exception as e:
                    print(f"page {p_num}: fetch failed {e}"); continue
                print(f"page {p_num}: {len(jobs)} cards")
                rows = [{"kind": "jobposting", "source_host": "www.stepstone.de", "source_url": j["url"],
                         "payload": j, "collector": "playwright-egress-v1", "client_id": CID} for j in jobs]
                if do_discover:
                    rows += discover_ats(jobs, discover_max)
                total += save_rows(rows, "stepstone"); time.sleep(1)

        elif mode == "stepstone-matrix":
            cities = [c for c in (args[0].split("|") if args else CITIES) if c]
            kws = args[1].split("|") if len(args) > 1 else KEYWORDS
            seen = set()
            urls = stepstone_matrix_urls(cities, kws)
            print(f"{len(urls)} listing URLs ({len(cities)} cities x {len(kws)} keywords)")
            for url in urls:
                try:
                    page, ctx = fetch_page(url)
                    jobs = parse_stepstone_pw(page, url)
                    page.close(); ctx.close()
                except Exception as e:
                    print(f"fetch failed {url[-50:]} {str(e)[:60]}"); continue
                jobs = [j for j in jobs if j["url"] not in seen]
                seen.update(j["url"] for j in jobs)
                rows = [{"kind": "jobposting", "source_host": "www.stepstone.de", "source_url": j["url"],
                         "payload": j, "collector": "playwright-egress-v1", "client_id": CID} for j in jobs]
                if do_discover:
                    rows += discover_ats(jobs, discover_max)
                n = save_rows(rows, "stepstone")
                total += n
                print(f"{url[-55:]:<55} new cards {len(jobs)} saved {n}")
                time.sleep(0.5)

        elif mode == "helios-detail":
            for url in args:
                try:
                    page, ctx = fetch_page(url)
                    jsonld_texts = parse_jsonld_pw(page)
                    page.close(); ctx.close()
                except Exception as e:
                    print(f"fetch failed {url[-45:]} {e}"); continue
                md = " ".join(jsonld_texts)
                jobs = parse_jsonld_lines(md, url)
                print(f"{url[-45:]} {len(jobs)} jobs")
                total += save_rows(
                    [{"kind": "jobposting", "source_host": "www.helios-gesundheit.de", "source_url": url,
                      "payload": j, "collector": "playwright-egress-v1", "client_id": CID} for j in jobs],
                    "helios")

        elif mode == "url":
            for url in args:
                try:
                    page, ctx = fetch_page(url)
                    jsonld_texts = parse_jsonld_pw(page)
                    link_md = parse_links_as_md(page)
                    page.close(); ctx.close()
                except Exception as e:
                    print(f"fetch failed {url[-60:]} {e}"); continue
                host = re.sub(r"^https?://([^/]+).*", r"\1", url)
                md = " ".join(jsonld_texts)
                jobs = parse_jsonld_lines(md, url)
                links = parse_generic_links(link_md, url)
                rows = [{"kind": "jobposting", "source_host": host, "source_url": url,
                         "payload": j, "collector": "playwright-egress-v1", "client_id": CID} for j in jobs]
                if links:
                    rows.append({"kind": "listing", "source_host": host, "source_url": url,
                                 "payload": {"links": links, "page": url},
                                 "collector": "playwright-egress-v1", "client_id": CID})
                print(f"{url[-60:]} jobposting {len(jobs)} links {len(links)}")
                total += save_rows(rows, "generic")

        print(f"total rows saved: {total}")
        print(f"output dir: {OUTPUT_DIR.resolve()}")

    finally:
        _close_browser()


if __name__ == "__main__":
    main(sys.argv[1:] or ["stepstone", "1-2"])
