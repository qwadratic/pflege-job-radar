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


def umantis(f, kez, town):
    """Haufe umantis: recruitingapp-<n>.de.umantis.com/Jobs/1?CompanyID=…  (Jobs/2, Jobs/3 pagination); details heuristic."""
    r = _get(f["career"]); html = r.text if r else ""
    m = re.search(r'https?://([a-z0-9\-\.]+\.umantis\.com)(/Jobs/\d+[^"\'\s<>]*)?', html)
    if not m: return None
    netloc = m.group(1); path = m.group(2) or "/Jobs/1"
    base = f"https://{netloc}"; first = base + path.replace("&amp;", "&")
    q = first.split("?", 1)[1] if "?" in first else ""
    extra = [f"{base}/Jobs/{i}" + (f"?{q}" if q else "") for i in range(2, 6)] + [base + "/Jobs/All"]
    return _base(f["name"], kez, town, first, [netloc], extra, (), "umantis")


BUILDERS = {"rexx": rexx, "dvinci": dvinci, "mein-check-in": mein_check_in, "umantis": umantis}
