"""Adapter-specific red tests for rexx (TASK-32), offline -- mocked HTTP via crawlers.vendor_adapters.get,
no network. The live tests/test_adapter_completeness.py run already caught both bugs below on real
boards; these pin the fixes down so a regression shows up without a slow live run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402

CU = "https://jobs.schoen-klinik.de/stellenangebote.html?search_mode=0&search_mode=job_filter_advanced"
CANONICAL = "https://jobs.schoen-klinik.de/stellenangebote.html"
DETAIL = "https://jobs.schoen-klinik.de/gesundheits-und-krankenpfleger-de-j111.html"


def _listing(url):
    return _R('<a href="/gesundheits-und-krankenpfleger-de-j111.html">Gesundheits- und Krankenpfleger (m/w/d)</a>',
               url=url, ok=True)


def test_rexx_populates_employment_type_from_detail_page_json_ld(monkeypatch):
    # field completeness (TASK-26/27 harness): every rexx detail page's own JobPosting JSON-LD names
    # employmentType plainly -- dropping it is a bug, not a source limitation.
    detail = _R(
        '<h1>Gesundheits- und Krankenpfleger (m/w/d)</h1>'
        '<script type="application/ld+json">{"@type":"JobPosting",'
        '"title":"Gesundheits- und Krankenpfleger (m/w/d)","employmentType":"FULL_TIME"}</script>',
        url=DETAIL, ok=True)
    mapping = {CANONICAL: _listing(CANONICAL), DETAIL: detail}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_rexx({"name": "Schön Klinik", "careers_url": CANONICAL})
    assert len(rows) == 1
    assert rows[0]["payload"]["employmentType"] == "FULL_TIME"


def test_rexx_covers_the_canonical_listing_path_even_when_careers_url_is_a_filtered_variant(monkeypatch):
    # read-path coverage (TASK-26/27 harness): every skin's own template links the plain, query-free
    # /stellenangebote.html regardless of which variant `careers_url` is -- a board whose registry
    # careers_url carries a search-filter query (e.g. Schön Klinik's ?search_mode=...) must still hit
    # that bare path, or the client's own read path goes uncovered.
    calls = []
    detail = _R("<h1>Gesundheits- und Krankenpfleger (m/w/d)</h1>", url=DETAIL, ok=True)
    mapping = {CU: _listing(CU), CANONICAL: _listing(CANONICAL), DETAIL: detail}
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    va.crawl_rexx({"name": "Schön Klinik", "careers_url": CU})
    assert CANONICAL in calls


def test_rexx_skips_the_canonical_merge_when_careers_url_is_a_client_filter(monkeypatch):
    # A `filter[client_id][]=...` query is not a cosmetic skin variant like ?search_mode= above -- it
    # is how a shared multi-tenant board (several distinct legal entities under one AG/group) narrows
    # to one clinic's own jobs. Merging the bare canonical page back in would re-add every sibling
    # entity's postings (confirmed live 2026-09-23: Gesundheitswelt Chiemgau AG's shared rexx board
    # lists a spa/wellness resort and Reha centres alongside Simssee Klinik on the unfiltered page).
    filtered = "https://karriere.gesundheitswelt.de/stellenangebote.html?filter[client_id][]=3"
    canonical = "https://karriere.gesundheitswelt.de/stellenangebote.html"
    own_detail = "https://karriere.gesundheitswelt.de/pflegefachkraft-de-j200.html"
    other_detail = "https://karriere.gesundheitswelt.de/therapeut-de-j999.html"
    calls = []
    mapping = {
        filtered: _R('<a href="/pflegefachkraft-de-j200.html">Pflegefachkraft (m/w/d)</a>', url=filtered, ok=True),
        canonical: _R('<a href="/pflegefachkraft-de-j200.html">Pflegefachkraft (m/w/d)</a>'
                       '<a href="/therapeut-de-j999.html">Physiotherapeut (m/w/d)</a>', url=canonical, ok=True),
        own_detail: _R("<h1>Pflegefachkraft (m/w/d)</h1>", url=own_detail, ok=True),
        other_detail: _R("<h1>Physiotherapeut (m/w/d)</h1>", url=other_detail, ok=True),
    }
    monkeypatch.setattr(va, "get", _router(mapping, calls))
    rows = va.crawl_rexx({"name": "Simssee Klinik", "careers_url": filtered})
    assert canonical not in calls                          # the unfiltered sibling-entity page is never even fetched
    assert len(rows) == 1 and "Pflegefachkraft" in rows[0]["payload"]["title"]   # not the other entity's job


def _sample(name):
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "board_samples", name), encoding="utf-8") as f:
        return f.read()


class _ChromeWalledSession:
    """bewerberportal.rhoen-klinikum-ag.com as measured live 2026-09-29 (TASK-168): HTTP 401 with an
    empty body to every request whose User-Agent claims Chrome (this module's shared UA and a current
    Windows Chrome UA alike, with or without sec-ch-ua client hints), the real page to a Firefox, curl or
    python-requests UA. Goes through the real get(), so the UA it picks is what is under test."""
    def __init__(self, pages):
        self.pages = pages

    def get(self, u, headers=None, **kw):
        if "Chrome/" in (headers or {}).get("User-Agent", ""):
            return _R("", url=u, ok=False)
        return self.pages.get(u, _R(ok=False, url=u))


def test_rexx_reads_the_rhoen_board_that_401s_a_chrome_user_agent():
    # Every nightly run from 2026-09-24 logged this board as "0 rows ... 0s" (a transport failure: the
    # first listing request 401'd, so crawl_rexx never saw a page) -- its 337 postings, 18 of them
    # nursing roles at RHÖN-KLINIKUM Campus Bad Neustadt (67308, 750 beds), went unread.
    cu = "https://bewerberportal.rhoen-klinikum-ag.com/stellenangebote.html"
    detail = "https://bewerberportal.rhoen-klinikum-ag.com/Altenpfleger-mwd-Zentrum-fuer-klinische-Medizin-de-j1232.html"
    s = _ChromeWalledSession({
        cu: _R(_sample("rexx_rhoen_stellenangebote_sample.html"), url=cu, ok=True),
        cu + "?start=100": _R(_sample("rexx_rhoen_stellenangebote_start400_sample.html"), url=cu + "?start=100", ok=True),
        detail: _R(_sample("rexx_rhoen_detail_j1232_sample.html"), url=detail, ok=True),
    })
    rows = va.crawl_rexx({"name": "RHÖN-KLINIKUM Campus Bad Neustadt a.d. Saale", "careers_url": cu,
                          "town": "Bad Neustadt a. d. Saale"}, session=s)
    assert [(r["source_url"], r["payload"]["title"], r["payload"]["org"], r["payload"]["loc"][0]["city"],
             r["payload"]["employmentType"]) for r in rows] == [
        (detail, "Altenpfleger (m/w/d) Zentrum für klinische Medizin", "RHÖN-KLINIKUM AG",
         "Bad Neustadt an der Saale", "FULL_TIME")]
