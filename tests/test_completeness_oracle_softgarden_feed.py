"""Adapter-specific completeness regressions for oracle + softgarden feed (TASK-31) -- the shared
harness in tests/test_adapter_completeness.py only checks datePosted/employmentType/description/city
(any row) and never validThrough, and it never proves host discovery avoids softgarden's own asset
CDN, so these gaps would never turn red there. Pure functions -> fake session/monkeypatch, no network.

  cdn host      softgarden's own certificate/app.softgarden.io CDN link on a tenant's page must never
                win host discovery over the real tenant host (crawlers/vendor_adapters.py and
                pflege_jobs/sources/softgarden.py both call this).
  validThrough  the feed's own JobPosting items never carry validThrough (verified live 2026-09-10:
                Klinikum Bayreuth 116/116, main-klinik 22/22 missing it) though each item's own
                detail page does -- fetch_feed must backfill it, not leave it dropped.
  wp fallback   crawl_oracle's crawl_wp_jobs fallback (Klinikum FFB, Altmühlfranken) returns rows with
                no employmentType/datePosted at all (verified live: 0/16 and 0/6) -- the enrichment
                pass must backfill employmentType from body text already fetched and datePosted from
                the WP SEO plugin's own <meta> tag.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources.softgarden import find_host, fetch_feed  # noqa: E402
from crawlers import vendor_adapters as VA  # noqa: E402


class FakeResp:
    def __init__(self, text="", status_code=200, url="", json_data=None, ok=None):
        self.text, self.status_code, self.url = text, status_code, url
        self.ok = ok if ok is not None else status_code == 200
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    """url -> FakeResp map; anything unlisted 404s."""
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        return self.routes.get(url) or FakeResp(status_code=404, url=url)


# ---------------------------------------------------------------------------------------------
# cdn host: certificate.softgarden.io / app.softgarden.io must never win over the real tenant host
# ---------------------------------------------------------------------------------------------
def test_find_host_skips_certificate_cdn_for_real_tenant_link():
    html = ('<img src="https://certificate.softgarden.io/badge.png">'
            '<a href="https://echt-tenant.career.softgarden.de/vacancies">Jobs</a>')
    s = FakeSession({"https://klinik.example.de/": FakeResp(text=html, url="https://klinik.example.de/")})
    host, evidence = find_host("https://klinik.example.de/", session=s)
    assert host == "https://echt-tenant.career.softgarden.de"
    assert evidence == "link:echt-tenant.career.softgarden.de"


def test_find_host_returns_none_when_only_cdn_hosts_present():
    html = '<img src="https://certificate.softgarden.io/badge.png"><img src="https://app.softgarden.io/x.png">'
    s = FakeSession({"https://klinik.example.de/": FakeResp(text=html, url="https://klinik.example.de/")})
    host, evidence = find_host("https://klinik.example.de/", session=s)
    assert (host, evidence) == (None, None)


# ---------------------------------------------------------------------------------------------
# validThrough: the feed drops it, the detail page has it -- fetch_feed must backfill
# ---------------------------------------------------------------------------------------------
def test_fetch_feed_backfills_missing_valid_through_from_detail_page():
    feed = {"dataFeedElement": [{"item": {
        "@type": "JobPosting", "title": "Pflegefachkraft (m/w/d)",
        "url": "https://tenant.example/jobs/1/Pflegefachkraft/", "datePosted": "2026-07-30",
    }}]}
    detail_html = ('<script type="application/ld+json">{"@type": "JobPosting", '
                   '"validThrough": "2028-07-30T10:19:31+02:00"}</script>')
    s = FakeSession({
        "https://tenant.example/jobs.feed.json": FakeResp(status_code=200, json_data=feed,
                                                           url="https://tenant.example/jobs.feed.json"),
        "https://tenant.example/jobs/1/Pflegefachkraft/": FakeResp(text=detail_html, url="https://tenant.example/jobs/1/Pflegefachkraft/"),
    })
    items, host = fetch_feed(["https://tenant.example"], session=s)
    assert host == "https://tenant.example"
    assert items[0]["validThrough"] == "2028-07-30T10:19:31+02:00"


def test_fetch_feed_leaves_valid_through_alone_when_feed_already_has_it():
    feed = {"dataFeedElement": [{"item": {
        "@type": "JobPosting", "title": "x", "url": "https://tenant.example/jobs/1/x/",
        "validThrough": "2027-01-01",
    }}]}
    s = FakeSession({"https://tenant.example/jobs.feed.json":
                      FakeResp(status_code=200, json_data=feed, url="https://tenant.example/jobs.feed.json")})
    items, _host = fetch_feed(["https://tenant.example"], session=s)
    assert items[0]["validThrough"] == "2027-01-01"  # no detail fetch triggered -- feed value kept


# ---------------------------------------------------------------------------------------------
# crawl_oracle's crawl_wp_jobs fallback: backfill employmentType/datePosted onto rows the fallback
# itself never sets either field for (parse_job_page has no such extraction, see vendor_adapters.py)
# ---------------------------------------------------------------------------------------------
def test_enrich_wp_fallback_backfills_employment_type_from_description_no_extra_fetch():
    rows = [{"source_url": "https://x.example/a/", "payload": {
        "url": "https://x.example/a/", "description": "Wir suchen Sie in Vollzeit ab sofort."}}]
    calls = []

    def spy_get(u, timeout=30, session=None):
        calls.append(u)
        return FakeResp(status_code=404, url=u)

    orig = VA.get
    VA.get = spy_get
    try:
        out = VA._enrich_wp_fallback_fields(rows, session=None)
    finally:
        VA.get = orig
    assert out[0]["payload"]["employmentType"] == "Vollzeit"
    # datePosted still missing (detail fetch 404s) -- but the description-derived field needed none
    assert calls == ["https://x.example/a/"]  # datePosted enrichment still attempted its own fetch


def test_enrich_wp_fallback_backfills_date_posted_from_wp_seo_meta():
    rows = [{"source_url": "https://x.example/a/", "payload": {
        "url": "https://x.example/a/", "description": "no keyword here"}}]
    s = FakeSession({"https://x.example/a/": FakeResp(
        text='<meta property="og:updated_time" content="2026-09-10T13:43:44+02:00" />',
        url="https://x.example/a/")})
    out = VA._enrich_wp_fallback_fields(rows, session=s)
    assert out[0]["payload"]["datePosted"] == "2026-09-10"
    assert "employmentType" not in out[0]["payload"] or not out[0]["payload"]["employmentType"]
