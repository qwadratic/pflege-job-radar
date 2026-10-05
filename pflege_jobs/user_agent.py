"""Per-host User-Agent overrides: the one table both HTTP clients of a board read -- the crawler
(crawlers/vendor_adapters.get) and the verify step's plain-HTTP rung (pflege_jobs/verify.py). Each
keeps its own default UA for every other host. Lived inside vendor_adapters.py until TASK-173: verify
never saw it, so a host the crawler reached with its override refused every verify request (RHÖN's
18 Bad Neustadt postings, escalated to a paid Firecrawl scrape each night)."""
from urllib.parse import urlparse

# augencentrum.de's CleanTalk anti-crawler plugin served a JS-cookie-challenge page to this repo's own
# UA (confirmed live 2026-09-22, TASK-114) while a plain Windows-Chrome UA sailed through first try;
# keyed by netloc, not folded into a shared header dict, so one site's quirk can never leak into the
# default every other board is fetched with.
UA_OVERRIDE = {
    "augencentrum.de": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    # RHÖN-KLINIKUM AG's rexx board answers HTTP 401 with an empty body to any UA claiming Chrome
    # (the shared UA above, a current Windows Chrome UA, with or without sec-ch-ua hints) and 200 to
    # Firefox, curl and python-requests UAs -- confirmed live 2026-09-29, TASK-168: every nightly run
    # from 2026-09-24 read 0 of its 337 postings as a transport failure.
    "bewerberportal.rhoen-klinikum-ag.com": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
}


def ua_override(u):
    """The UA UA_OVERRIDE lists for u's host (the domain itself or any subdomain of it), else None.
    A missing url is no host (urlparse(None) is bytes): the caller's own request then fails as before."""
    host = urlparse(u or "").netloc.lower()
    for domain, ua in UA_OVERRIDE.items():
        if host == domain or host.endswith("." + domain):
            return ua
    return None
