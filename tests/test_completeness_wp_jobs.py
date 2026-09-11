"""Adapter-specific completeness regressions for wp_jobs (TASK-35) -- the shared harness in
tests/test_adapter_completeness.py covers the five generic checks against live boards; this module
locks in fixes the live run found that the generic checks can't exercise offline (mocked HTTP via
crawlers.vendor_adapters.get, no network):

  widget listing   a TYPO3 "klinikum-jobs" division page (München Klinik and siblings) renders an
                   empty <ul class="job-results"> -- real postings sit only in an embedded
                   `var allJobs = [...]` JSON blob. Before the fix, crawl_wp_jobs took the division
                   page's OWN <h1> (a marketing headline, e.g. "HEILEN KOENNEN.") as a fake job
                   title instead of walking that blob's own per-posting links -- verified missing
                   live 2026-09-10 (read-path coverage + 0/20 field completeness on
                   muenchen-klinik.de before the fix).
  redirect collapse a stale job slug that 200s instead of 404ing (seen live: medbo.de) redirects to
                   one shared generic landing page ("Jetzt einsteigen!") instead -- every different
                   dead slug that lands there must collapse to at most one row, not one bogus
                   duplicate per dead slug.
  employmentType   parse_job_page's JSON-LD branch (shared by every board on this vendor) read
                   title/org/loc/dates/description but silently dropped employmentType even when
                   the source's own JobPosting JSON-LD names it plainly -- verified missing live
                   2026-09-10 (0/20 rows on muenchen-klinik.de before the fix).
  Ausschreibung date a non-WordPress board (InnKlinikum) publishes no SEO-plugin meta date at all,
                   but does label its own posting window in the body ("Interne Ausschreibung: vom
                   DD.MM.YYYY bis DD.MM.YYYY") -- _enrich_wp_fallback_fields must read the "vom"
                   date as datePosted rather than give up (0/56 rows before the fix).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402


def _jsonld_job(title, **extra):
    d = {"@type": "JobPosting", "title": title}
    d.update(extra)
    import json
    return '<script type="application/ld+json">%s</script>' % json.dumps(d)


# --- klinikum-jobs widget: division page is a listing, never a posting -------------------------

def test_widget_division_page_is_never_itself_a_row_its_embedded_jobs_are(monkeypatch):
    cu = "https://www.muenchen-klinik.de/jobs/"
    division = "https://www.muenchen-klinik.de/jobs/pflege/"
    detail = "https://www.muenchen-klinik.de/stellenmarkt/stellenangebot/hebamme-44771/"
    cu_page = _R('<a href="%s">Pflegedienst</a>' % division, url=cu, ok=True)
    division_page = _R(
        '<h1>PFLEGEN KOENNEN.</h1><ul class="job-results"></ul>'
        '<script>var allJobs = [{"title":"ignored teaser","link":"%s"}];</script>' % detail,
        url=division, ok=True)
    detail_page = _R(_jsonld_job("Hebamme / Entbindungspfleger (m|w|d)", employmentType="PART_TIME"),
                      url=detail, ok=True)
    mapping = {cu: cu_page, division: division_page, detail: detail_page}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_wp_jobs({"name": "München Klinik", "careers_url": cu})
    titles = [r["payload"]["title"] for r in rows]
    assert titles == ["Hebamme / Entbindungspfleger (m|w|d)"]  # not "PFLEGEN KOENNEN."


# --- a stale slug's shared soft-404 landing page must collapse, not duplicate -------------------

def test_two_dead_slugs_redirecting_to_the_same_landing_page_collapse_to_one_row(monkeypatch):
    cu = "https://www.medbo.de/karriere/jobsmedbo"
    dead_a = "https://www.medbo.de/karriere/jobsmedbo/detail/dead-slug-a"
    dead_b = "https://www.medbo.de/karriere/jobsmedbo/detail/dead-slug-b"
    landing = "https://www.medbo.de/test-jobs"
    cu_page = _R('<a href="%s">A</a><a href="%s">B</a>' % (dead_a, dead_b), url=cu, ok=True)
    # both dead slugs 200 but the response's OWN .url shows the shared redirect target -- exactly
    # how `requests` reports a followed redirect (resp.url != the url that was requested).
    landing_page = _R("<h1>Jetzt einsteigen!</h1>", url=landing, ok=True)
    mapping = {cu: cu_page, dead_a: landing_page, dead_b: landing_page}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_wp_jobs({"name": "Medbo", "careers_url": cu})
    assert len(rows) == 1
    assert rows[0]["payload"]["url"] == landing


# --- field completeness: JSON-LD employmentType must not be dropped ------------------------------

def test_parse_job_page_keeps_json_ld_employment_type():
    html = _jsonld_job("Pflegefachkraft (m/w/d)", employmentType="FULL_TIME")
    j = va.parse_job_page(html, "https://x/stellen/pflegefachkraft", "Klinikum")
    assert j["employmentType"] == "FULL_TIME"


# --- datePosted backfill: "Interne Ausschreibung: vom DD.MM.YYYY bis DD.MM.YYYY" -----------------

def test_enrich_reads_ausschreibung_vom_date_when_no_wp_seo_meta_exists(monkeypatch):
    detail = "https://www.innklinikum.de/stellenangebote-detailansicht/abc"
    rows = [va.row("www.innklinikum.de", detail,
                    {"title": "Pflegefachkraft (m/w/d)", "url": detail, "description": "Vollzeit"}, "wp_jobs")]
    page = _R('<div class="offer-heading">Interne Ausschreibung:</div>'
              '<div class="offer-text">vom 26.08.2026 bis 08.09.2026</div>', url=detail, ok=True)
    monkeypatch.setattr(va, "get", _router({detail: page}))
    out = va._enrich_wp_fallback_fields(rows)
    assert out[0]["payload"]["datePosted"] == "2026-08-26"
