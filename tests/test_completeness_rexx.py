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
