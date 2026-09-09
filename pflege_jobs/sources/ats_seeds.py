"""Seed builders per ATS family (source employer_ats). Each returns a Crawler seed or None.
Detection input = census row {name, city, career, ats}. The generic listing-first crawler (career_crawl.Crawler) does the rest.
"""
import re, requests
from urllib.parse import urlparse, urljoin
from .career_crawl import UA

S = requests.Session(); S.headers.update({"User-Agent": UA})


def _get(u):
    try: return S.get(u, timeout=30, allow_redirects=True)
    except requests.RequestException: return None


NATIONWIDE = re.compile(r"schoen-klinik|schön|helios|sana\b|asklepios|rhoen|rhön|paracelsus|median|mediclin|bg-?klinik|bgu|cjd|augustinum|vamed|ameos|artemed|vitos|klinikverbund-gesetzlichen|diakoneo|caritas|malteser|johanniter|drk|brk", re.I)


def _base(seed_name, kez, town, career, hosts, extra=(), sitemaps=(), ats=""):
    nationwide = bool(NATIONWIDE.search(career + " " + seed_name))
    return {"name": seed_name, "kez": kez, "town": town, "career": career, "extra_seeds": list(extra), "sitemaps": list(sitemaps),
            "hosts": list(dict.fromkeys(hosts)), "bavaria_only_operator": not nationwide, "ats": ats}


def rexx(f, kez, town):
    """rexx systems portals: <host>/stellenangebote.html (server-rendered list, ?page= pagination), JSON-LD on details."""
    p = urlparse(f["career"]); host = f"{p.scheme}://{p.netloc}"
    lst = host + "/stellenangebote.html"
    extra = [lst + f"?page={i}" for i in range(2, 7)] + [lst + "?search_mode=0"]
    return _base(f["name"], kez, town, lst, [p.netloc], extra, [host + "/sitemap.xml"], "rexx")


def dvinci(f, kez, town):
    """d.vinci: <host>/de/jobs (+ /de/jobs/iframe, ?page=), JSON-LD on details. Host = career host if it serves /de/jobs, else link found on page."""
    r = _get(f["career"]); html = r.text if r else ""
    m = re.search(r'https?://([a-z0-9\.\-]+\.dvinci(?:-hr|-easy)?\.(?:com|de))', html) or re.search(r'https?://([a-z0-9\.\-]+)/de/jobs', html)
    p = urlparse(f["career"])
    netloc = m.group(1) if m else p.netloc
    host = f"https://{netloc}"
    lst = host + "/de/jobs"
    return _base(f["name"], kez, town, lst, [netloc, p.netloc], [lst + "/iframe"] + [lst + f"?page={i}" for i in range(2, 6)], [host + "/sitemap.xml"], "dvinci")


def mein_check_in(f, kez, town):
    """mein-check-in.de/<slug>/overview lists positions (/position-<id>); details heuristic (no JSON-LD)."""
    slug = None
    m = re.search(r"mein-check-in\.de/([a-z0-9\-]+)", f["career"] or "")
    if m: slug = m.group(1)
    else:
        r = _get(f["career"]); m = re.search(r"mein-check-in\.de/([a-z0-9\-]+)", r.text if r else "")
        if m: slug = m.group(1)
    if not slug: return None
    lst = f"https://www.mein-check-in.de/{slug}/overview"
    return _base(f["name"], kez, town, lst, ["www.mein-check-in.de"], [f"https://www.mein-check-in.de/{slug}/", f"https://www.mein-check-in.de/{slug}/overview?page=2"], (), "mein-check-in")


_UMANTIS_HOST = re.compile(r"^recruitingapp-\d+\.[a-z]{2}\.umantis\.com$", re.I)
_UMANTIS_HOP = re.compile(r"/stellenangebot|karriere.*stellen", re.I)


def umantis(f, kez, town):
    """Haufe umantis: recruitingapp-<n>.de.umantis.com/Jobs/1?CompanyID=…  (Jobs/2, Jobs/3 pagination); details heuristic.

    Some careers_url pages carry only relative /Vacancies/<id>/Description/1 links (no absolute
    umantis URL in the HTML) -- e.g. https://recruitingapp-5545.de.umantis.com/Jobs/1?lang=ger itself.
    Others are a CMS hub one hop away from the page that actually embeds absolute umantis URLs
    -- e.g. ANregiomed's /karriere-jobs/ -> /karriere-jobs/stellenangebote-bewerbung/stellenangebote/.

    Section-first (checked live 2026-09-07, not just from the earlier survey): some umantis boards
    (e.g. recruitingapp-5545) DO render a real job-function facet -- a "Unternehmensbereich" <select
    name="searchFunction"> with an <option value="10020">Pflegedienst</option> alongside Ärztlicher
    Dienst/Therapie/Verwaltung/... -- which looked promising. But that field is submitted via
    <form method="post" action="/Jobs/1">, and appending it as a GET query param
    (?searchFunction=10020, case variants, ?function=10020) returned byte-identical page sizes to the
    unfiltered listing -- i.e. silently ignored, exactly like the rexx/concludis-widget attempts in the
    survey. Other umantis boards (recruitingapp-5656/CompanyID=All) only expose a "Klinik/Fachbereich"
    org-unit picker, not a job-function one at all. Neither shape gives this GET-only seed builder a
    working narrower URL, so umantis intentionally keeps the full board-URL-discovery fallback below
    unchanged; career_crawl.Crawler's own section-first BFS gate still gets a shot at the *rendered*
    listing page's job-level anchors once a seed is built.
    """
    career = f["career"] or ""
    p = urlparse(career)
    if _UMANTIS_HOST.match(p.netloc):
        # careers_url IS the umantis board itself; no absolute umantis URL needed in the HTML.
        netloc = p.netloc; base = f"https://{netloc}"; first = career
        q = first.split("?", 1)[1] if "?" in first else ""
        extra = [f"{base}/Jobs/{i}" + (f"?{q}" if q else "") for i in range(2, 6)] + [base + "/Jobs/All"]
        return _base(f["name"], kez, town, first, [netloc], extra, (), "umantis")

    r = _get(career); html = r.text if r else ""
    m = re.search(r'https?://([a-z0-9\-\.]+\.umantis\.com)(/Jobs/\d+[^"\'\s<>]*)?', html)
    hub_url = None
    if not m and r is not None:
        # One hop: careers_url is a CMS hub; follow a link that looks like the real job-listing page
        # and re-run the umantis regex there. Allow a same-registrable-domain subdomain hop (e.g.
        # karriere-im.<site> from <site>'s own /karriere page), not just an exact host match -- some
        # operators run the umantis-embedding page on a different subdomain than careers_url.
        reg_domain = ".".join(p.netloc.split(".")[-2:])
        for href in re.findall(r'href="([^"]+)"', html):
            link = urljoin(career, href)
            link_netloc = urlparse(link).netloc
            if ".".join(link_netloc.split(".")[-2:]) != reg_domain: continue
            # Within the same registrable domain, a distinct subdomain (e.g. karriere-im.<site> from
            # <site>'s own /karriere page) is itself a strong enough "this is the careers portal"
            # signal even when the link's own path/anchor carries no "stellen" text (confirmed live:
            # Klinikverbund Allgäu's hub only labels this link "Offene Stellen" in nearby markup, not
            # in the href or its own host name) -- only same-host links still need the path match.
            if link_netloc == p.netloc and not _UMANTIS_HOP.search(link): continue
            r2 = _get(link); html2 = r2.text if r2 else ""
            m = re.search(r'https?://([a-z0-9\-\.]+\.umantis\.com)(/Jobs/\d+[^"\'\s<>]*)?', html2)
            if m:
                # The hop page itself sometimes already lists the real /Vacancies/<id> job links
                # directly (no /Jobs/<n> path found on it at all) -- a guessed .../Jobs/1 fallback
                # from just the bare umantis host can then land on a *different*, narrower listing
                # than what the hub page actually shows (confirmed live: ANregiomed's own hub page
                # lists ~100 vacancies incl. nursing roles the guessed Jobs/1..5 pagination never
                # surfaces). Prefer the hub page itself as the seed's start URL in that case.
                if not m.group(2) and re.search(r"/Vacancies/\d+", html2):
                    hub_url = link
                break
    if not m: return None
    netloc = m.group(1); path = m.group(2) or "/Jobs/1"
    base = f"https://{netloc}"
    guessed = base + path.replace("&amp;", "&")
    first = hub_url or guessed
    q = guessed.split("?", 1)[1] if "?" in guessed else ""
    extra = [f"{base}/Jobs/{i}" + (f"?{q}" if q else "") for i in range(2, 6)] + [base + "/Jobs/All"]
    if hub_url:
        extra = [guessed] + extra   # still top up with the guessed listing, just not as the primary seed
    return _base(f["name"], kez, town, first, [netloc], extra, (), "umantis")


BUILDERS = {"rexx": rexx, "dvinci": dvinci, "mein-check-in": mein_check_in, "umantis": umantis}
