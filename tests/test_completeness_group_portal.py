"""Adapter-specific completeness regressions for group_portal (TASK-36) -- the shared harness in
tests/test_adapter_completeness.py covers the five generic checks against live boards; this module
locks in fixes the live run found that the generic checks can't exercise offline (mocked HTTP via
crawlers.vendor_adapters.get, no network):

  career-facts fallback   karriere.barmherzige.net's job pages carry no JobPosting JSON-LD at all
                          (only a generic WebSite/Organization graph) -- parse_job_page's non-JSON-LD
                          fallback took title/description but dropped employmentType/city even though
                          the page states them plainly in a <ul class="career-facts"> icon+label list
                          -- verified missing live 2026-09-10 (0/84 rows on barmherzige-regensburg.de,
                          klinikum-straubing.de, barmherzige-bieten-zukunft.de before the fix).
  pagination cap          GROUP_PORTALS carried a "pages": 12 cap on both kbo.de and
                          karriere.barmherzige.net -- a self-invented stop instead of the board's own
                          end signal (an empty page). Removed; the loop now only stops there.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402


def test_group_portal_reads_employment_type_and_city_from_career_facts_when_no_jsonld(monkeypatch):
    g = {"match": "barmherzige", "list": "https://karriere.barmherzige.net/jobs/",
         "page_param": "c_page",
         "job_rx": r"https://karriere\.barmherzige\.net/jobs/[a-z0-9][^\"'\s>?]+", "host": "karriere.barmherzige.net"}
    c = {"name": "St. Barbara Krankenhaus Schwandorf",
         "careers_url": "https://www.barmherzige-bieten-zukunft.de/stellenmarkt/stellenboerse"}
    detail_url = "https://karriere.barmherzige.net/jobs/pflegefachkraft-mwd-1"
    list_page = _R('<a href="%s">x</a>' % detail_url, url=g["list"], ok=True)
    detail = _R(
        '<h2>Pflegefachkraft (m/w/d)</h2>'
        '<ul class="career-facts">'
        '<li><img src="/icons/schedule.svg" class="career-icon" /><span class="fact">Vollzeit</span></li>'
        '<li><img src="/icons/location.svg" class="career-icon" /><span class="fact">Schwandorf</span></li>'
        '</ul><p>Beschreibung des Jobs.</p>', url=detail_url, ok=True)
    monkeypatch.setattr(va, "get", _router({g["list"]: list_page, detail_url: detail}))
    rows = va.crawl_group_portal(c, g)
    assert len(rows) == 1
    p = rows[0]["payload"]
    assert p["employmentType"] == "Vollzeit"
    assert p["loc"][0]["city"] == "Schwandorf"


def test_group_portal_paginates_past_the_old_page_cap_to_the_boards_own_end(monkeypatch):
    g = {"match": "barmherzige", "list": "https://karriere.barmherzige.net/jobs/",
         "page_param": "c_page",
         "job_rx": r"https://karriere\.barmherzige\.net/jobs/[a-z0-9][^\"'\s>?]+", "host": "karriere.barmherzige.net"}
    c = {"name": "St. Barbara Krankenhaus Schwandorf",
         "careers_url": "https://www.barmherzige-bieten-zukunft.de/stellenmarkt/stellenboerse"}
    # 15 pages of one fresh job each -- one more than the old hardcoded 12-page cap -- then an empty
    # page 16, the board's own end signal.
    mapping = {}
    for i in range(1, 16):
        url = g["list"] if i == 1 else "%s?c_page=%d" % (g["list"], i)
        job_url = "https://karriere.barmherzige.net/jobs/job-%d" % i
        mapping[url] = _R('<a href="%s">x</a>' % job_url, url=url, ok=True)
        mapping[job_url] = _R("<h2>Job Nr. %d (m/w/d)</h2><p>desc</p>" % i, url=job_url, ok=True)
    end_url = "%s?c_page=16" % g["list"]
    mapping[end_url] = _R("<html></html>", url=end_url, ok=True)
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_group_portal(c, g)
    assert len(rows) == 15
