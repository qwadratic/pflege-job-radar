"""Playwright/HTTP crawlers for the walled aggregator (Indeed) and the JS portals the
requests-based career crawler could not finish.

Every mode emits the same inbox-shaped row as crawlers/claude_egress.py:
    {kind, source_host, source_url, payload{title,org,loc[],url,page,description}, collector, client_id}
so crawlers/load_crawl_output.py ingests the output unchanged. Host decides the source:
pflege_jobs/sources/inbox.py routes indeed -> aggregator (40) and clinic hosts -> employer_ats (20).

  python crawlers/portals.py indeed                          # full q x l matrix
  python crawlers/portals.py indeed "München|Nürnberg"        # subset
  python crawlers/portals.py portals                         # all JS portals
  python crawlers/portals.py portals vinzenz muenchen-klinik  # subset
  python crawlers/portals.py list                            # show configured portals

Findings that shaped this file (probed 2026-09-06):
- Indeed serves .job_seen_beacon cards to a plain headless Chromium; no block. The title anchor's
  href is a /pagead/clk redirect that rotates per impression, so the stable data-jk is used to
  rebuild a canonical /viewjob?jk=<id> source_ref. Without that, every crawl would duplicate rows.
- St. Vinzenz and Klinikverbund Allgäu are both Haufe umantis (instances 5580 / 5556). The vacancy
  list is server-rendered, so plain HTTP is enough — no browser, no JS. Allgäu really does publish
  only ~10 vacancies; filtering the search form by Funktion/Standort returns the same set.
- Krankenhaus St. Josef is softgarden and publishes a schema.org DataFeed at /jobs.feed.json —
  richer (description, dates) than scraping, and cheap.
- München Klinik's JobFinder lives at /jobs/pflege/stellenangebote/ (not /jobs/pflege/), hides the
  list behind a consentmanager overlay, and lazy-renders rows on scroll.
- Klinikum Altmühlfranken renders job links server-side under /stellenangebote/ plus JSON-LD.
"""
import html as _html
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_egress import (CID, CITIES, fetch_page, parse_jsonld_pw, save_rows, _close_browser)  # noqa: E402

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0.0.0 Safari/537.36")

# gender marker: (m/w/d), (w|m|d), (m/w/x), (gn) ...
GENDER_MARK = r"\((?:m|w|d|x|i|gn)\s?[/|*]\s?(?:m|w|d|x|i|gn)(?:\s?[/|*]\s?(?:m|w|d|x|i|gn))?\)"


# ---------------------------------------------------------------------------
# Indeed
# ---------------------------------------------------------------------------
INDEED_KEYWORDS = ["pflegefachkraft", "gesundheits- und krankenpfleger", "intensivpflege",
                   "stationsleitung", "praxisanleiter", "ota", "hebamme", "pflegedienstleitung"]


def parse_indeed_pw(page, page_url):
    """Extract .job_seen_beacon cards from a rendered Indeed listing page."""
    cards = page.evaluate(
        "() => {"
        "  const txt = (el) => el ? (el.textContent || '').replace(/\\s+/g,' ').trim() : null;"
        "  return Array.from(document.querySelectorAll('.job_seen_beacon')).map(c => {"
        "    const a = c.querySelector('a.jcs-JobTitle') || c.querySelector('h2 a') || c.querySelector('a[data-jk]');"
        "    const aria = a ? (a.getAttribute('aria-label') || '') : '';"
        "    return {"
        "      jk: a ? a.getAttribute('data-jk') : null,"
        "      title: txt(c.querySelector('h2 span[title]')) || txt(a) || aria.replace(/^Alle Details zu\\s*/i,'') || null,"
        "      org: txt(c.querySelector('[data-testid=\"company-name\"], .companyName')),"
        "      city: txt(c.querySelector('[data-testid=\"text-location\"], .companyLocation')),"
        "      snippet: txt(c.querySelector('[data-testid=\"belowJobSnippet\"], .job-snippet')),"
        "      href: a ? a.href : null"
        "    };"
        "  });"
        "}")
    out = []
    for c in cards:
        if not c.get("title"):
            continue
        jk = c.get("jk")
        # data-jk is stable; the anchor href is a rotating /pagead/clk tracking redirect.
        url = "https://de.indeed.com/viewjob?jk=" + jk if jk else c.get("href")
        if not url:
            continue
        loc = (c.get("city") or "").strip()
        m = re.match(r"(\d{5})\b", loc)
        plz = m.group(1) if m else None
        city = re.sub(r"^\d{5}\s*", "", loc).split(",")[0].strip() or None
        out.append({"title": c["title"], "org": (c.get("org") or "").strip(),
                    "loc": [{"city": city, "plz": plz, "region": None}],
                    "url": url, "page": page_url,
                    "description": c.get("snippet") or None})
    return out


def indeed_urls(cities=None, keywords=None, pages=1):
    from urllib.parse import quote_plus
    out = []
    for c in (cities or CITIES):
        for k in (keywords or INDEED_KEYWORDS):
            for p in range(pages):
                u = "https://de.indeed.com/jobs?q=%s&l=%s" % (quote_plus(k), quote_plus(c))
                out.append(u + ("&start=%d" % (p * 10) if p else ""))
    return out


def crawl_indeed(cities=None, keywords=None, pages=1, sleep=1.0):
    urls = indeed_urls(cities, keywords, pages)
    print("%d Indeed listing URLs" % len(urls))
    seen, total = set(), 0
    for u in urls:
        try:
            page, ctx = fetch_page(u, wait_ms=6000)
        except Exception as e:
            print("fetch failed %-45s %s" % (u[-45:], str(e)[:60])); continue
        try:
            jobs = parse_indeed_pw(page, u)
        finally:
            page.close(); ctx.close()
        jobs = [j for j in jobs if j["url"] not in seen]
        seen.update(j["url"] for j in jobs)
        rows = [{"kind": "jobposting", "source_host": "de.indeed.com", "source_url": j["url"],
                 "payload": j, "collector": "playwright-indeed-v1", "client_id": CID} for j in jobs]
        n = save_rows(rows, "indeed"); total += n
        print("%-58s new %3d saved %3d" % (u[-58:], len(jobs), n))
        time.sleep(sleep)
    return total


# ---------------------------------------------------------------------------
# JS portal helpers
# ---------------------------------------------------------------------------
CONSENT_SELECTORS = ['#cmpwelcomebtnyes', 'button:has-text("Alle akzeptieren")',
                     'button:has-text("Akzeptieren")', 'button:has-text("Zustimmen")',
                     'button:has-text("Einverstanden")', '#onetrust-accept-btn-handler']


def dismiss_consent(page, wait_ms=3000):
    """Click the first consent button present. München Klinik hides its JobFinder behind one."""
    for sel in CONSENT_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=5000); page.wait_for_timeout(wait_ms); return sel
        except Exception:
            continue
    return None


def autoscroll(page, rounds=8, step=5000, wait_ms=1200):
    """Lazy-rendered lists only materialise what has been scrolled past."""
    for _ in range(rounds):
        page.mouse.wheel(0, step); page.wait_for_timeout(wait_ms)


def parse_mwd_anchors_pw(page, page_url, href_rx="", org=""):
    """Anchors whose text carries a gender marker; href_rx narrows to detail-page paths."""
    rows = page.evaluate(
        "(a2) => {"
        "  const [hrefRx, gmSrc] = a2;"
        "  const rx = hrefRx ? new RegExp(hrefRx, 'i') : null;"
        "  const gm = new RegExp(gmSrc, 'i');"
        "  return Array.from(document.querySelectorAll('a[href]'))"
        "    .map(a => ({t: (a.innerText || '').replace(/\\s+/g,' ').trim(), h: a.href}))"
        "    .filter(x => x.t && gm.test(x.t) && (!rx || rx.test(x.h)));"
        "}", [href_rx, GENDER_MARK])
    out, seen = [], set()
    for r in rows:
        if r["h"] in seen:
            continue
        seen.add(r["h"])
        # list rows read "<title> [Für unsere <dept>] [Standort: <city>] [Mehr erfahren]"
        title = re.split(r"\s+(?:Für unsere|Für unser|Standort:|Mehr erfahren)\s*", r["t"])[0].strip()
        st = re.search(r"Standort:\s*([^|\n]{2,60}?)(?:\s+Mehr erfahren|$)", r["t"])
        out.append({"title": title[:300], "org": org,
                    "loc": [{"city": st.group(1).strip() if st else None, "plz": None, "region": None}],
                    "url": r["h"], "page": page_url, "description": None})
    return out


UMANTIS_ROW = re.compile(
    r'<a[^>]+href="(?P<href>/Vacancies/(?P<id>\d+)/Description/\d+)"[^>]*>(?P<inner>.*?)</a>', re.S | re.I)


def parse_umantis(html, base, org="", page_url=""):
    """Haufe umantis renders /Jobs/1 server-side: every /Vacancies/<id>/Description anchor is there."""
    out, seen = [], set()
    for m in UMANTIS_ROW.finditer(html):
        vid = m.group("id")
        if vid in seen:
            continue
        title = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", m.group("inner")))).strip()
        if len(title) < 4:
            continue
        seen.add(vid)
        out.append({"title": title[:300], "org": org,
                    "loc": [{"city": None, "plz": None, "region": None}],
                    "url": base.rstrip("/") + m.group("href"),
                    "page": page_url or base, "description": None})
    return out


def parse_jobposting_feed(feed, page_url):
    """schema.org DataFeed of JobPosting (softgarden: <career-host>/jobs.feed.json)."""
    out = []
    for it in (feed.get("dataFeedElement") or []):
        j = (it or {}).get("item") or {}
        types = j.get("@type")
        types = types if isinstance(types, list) else [types]
        if "JobPosting" not in types:
            continue
        locs = j.get("jobLocation") or []
        locs = locs if isinstance(locs, list) else [locs]
        ls = []
        for l in locs:
            a = ((l or {}).get("address") or {})
            ls.append({"city": a.get("addressLocality"), "plz": a.get("postalCode"),
                       "region": a.get("addressRegion")})
        org = j.get("hiringOrganization") or {}
        out.append({"title": j.get("title"),
                    "org": org.get("name") if isinstance(org, dict) else org,
                    "datePosted": j.get("datePosted"), "validThrough": j.get("validThrough"),
                    "employmentType": j.get("employmentType"),
                    "loc": ls or [{"city": None, "plz": None, "region": None}],
                    "url": j.get("url") or page_url, "page": page_url,
                    "description": re.sub(r"<[^>]+>", " ", j.get("description") or "")[:20000] or None})
    return out


JS_PORTALS = {
    "muenchen-klinik": {
        "kind": "browser", "org": "München Klinik gGmbH", "host": "www.muenchen-klinik.de",
        "urls": ["https://www.muenchen-klinik.de/jobs/pflege/stellenangebote/",
                 "https://www.muenchen-klinik.de/stellenmarkt/"],
        "href_rx": r"stellenangebot", "consent": True, "scroll": 8,
        "town": "München", "plz": None,
    },
    "vinzenz": {
        "kind": "umantis", "org": "St. Vinzenz Klinik Pfronten",
        "base": "https://recruitingapp-5580.de.umantis.com", "host": "recruitingapp-5580.de.umantis.com",
        "town": "Pfronten", "plz": "87459",
    },
    "klinikverbund-allgaeu": {
        "kind": "umantis", "org": "Klinikverbund Allgäu",
        "base": "https://recruitingapp-5556.de.umantis.com", "host": "recruitingapp-5556.de.umantis.com",
        "town": "Kempten", "plz": "87439",
    },
    "josef": {
        "kind": "feed", "org": "Krankenhaus St. Josef Schweinfurt", "host": "karriere.josef.de",
        "urls": ["https://karriere.josef.de/jobs.feed.json"],
    },
    "altmuehlfranken": {
        "kind": "browser", "org": "Klinikum Altmühlfranken", "host": "karriere.klinikum-altmuehlfranken.de",
        "urls": ["https://karriere.klinikum-altmuehlfranken.de/"],
        "href_rx": r"/stellenangebote/", "consent": True, "scroll": 6,
        "town": "Weißenburg i.Bay.", "plz": "91781",
    },
}


def crawl_js_portal(name, cfg):
    """Return inbox-shaped rows for one JS portal."""
    host, org = cfg["host"], cfg.get("org", "")
    jobs = []
    if cfg["kind"] == "umantis":
        base = cfg["base"]
        r = requests.get(base + "/Jobs/1", headers={"User-Agent": UA}, timeout=45)
        r.raise_for_status()
        jobs = parse_umantis(r.text, base, org, base + "/Jobs/1")
    elif cfg["kind"] == "feed":
        for u in cfg["urls"]:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=45)
            r.raise_for_status()
            jobs += parse_jobposting_feed(r.json(), u)
    else:
        seen = set()
        for u in cfg["urls"]:
            try:
                page, ctx = fetch_page(u, wait_ms=4000)
            except Exception as e:
                print("  %s: fetch failed %s %s" % (name, u[-45:], str(e)[:60])); continue
            found = []
            try:
                if cfg.get("consent"):
                    dismiss_consent(page)
                autoscroll(page, cfg.get("scroll", 6))
                found = parse_mwd_anchors_pw(page, u, cfg.get("href_rx", ""), org)
                for t in parse_jsonld_pw(page):            # JSON-LD beats anchor text when present
                    try:
                        found += parse_jobposting_feed({"dataFeedElement": [{"item": json.loads(t)}]}, u)
                    except Exception:
                        pass
            finally:
                page.close(); ctx.close()
            for j in found:
                if j.get("url") and j["url"] not in seen:
                    seen.add(j["url"]); jobs.append(j)

    # Portals that never state a town on the list page still belong to a known site: fall back to the
    # operator's own town so registry matching and the Bavaria filter have something to work with.
    town, plz = cfg.get("town"), cfg.get("plz")
    rows = []
    for j in jobs:
        if not (j.get("title") and j.get("url")):
            continue
        locs = j.get("loc") or [{}]
        if town and not any((l or {}).get("city") for l in locs):
            j["loc"] = [{"city": town, "plz": plz, "region": "BAYERN"}]
        rows.append({"kind": "jobposting", "source_host": host, "source_url": j["url"],
                     "payload": j, "collector": "playwright-%s-v1" % name, "client_id": CID})
    return rows


def crawl_portals(names=None):
    total = 0
    for name in (names or list(JS_PORTALS)):
        cfg = JS_PORTALS.get(name)
        if not cfg:
            print("unknown portal: %s" % name); continue
        try:
            rows = crawl_js_portal(name, cfg)
        except Exception as e:
            print("%-24s FAILED %s" % (name, str(e)[:90])); continue
        n = save_rows(rows, "portal_" + name); total += n
        print("%-24s %-9s jobs %3d saved %3d" % (name, cfg["kind"], len(rows), n))
    return total


def main(argv):
    mode, args = (argv[0] if argv else "list"), argv[1:]
    total = 0
    try:
        if mode == "indeed":
            cities = [c for c in args[0].split("|") if c] if args else None
            kws = [k for k in args[1].split("|") if k] if len(args) > 1 else None
            pages = int(args[2]) if len(args) > 2 else 1
            total = crawl_indeed(cities, kws, pages)
        elif mode == "portals":
            total = crawl_portals(args or None)
        elif mode == "list":
            for k, v in JS_PORTALS.items():
                print("%-24s %-9s %s" % (k, v["kind"], v.get("base") or (v.get("urls") or [""])[0]))
            return
        else:
            print(__doc__); return
        print("total rows saved: %d" % total)
    finally:
        _close_browser()


if __name__ == "__main__":
    main(sys.argv[1:])
