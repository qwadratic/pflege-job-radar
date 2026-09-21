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
import pflege_jobs.sources.hr4you as hr4you_mod  # noqa: E402
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
    # crawl_wp_jobs's generic walk now correctly drops both dead slugs (pflege_jobs.verify's
    # _bounced_to_list contract, TASK-69), which empties `out` and falls through to the hr4you
    # capability probe (crawl_wp_jobs's own last-resort branch) -- that probe uses its own plain
    # `requests` call, not `va.get`/`_router`, so without this it made a real request to
    # www.medbo.de on every run of this "no network" file. Block it at its own network boundary,
    # the same way tests/test_completeness_beesite_hr4you.py's mutation tests do.
    monkeypatch.setattr(hr4you_mod, "_get", lambda *a, **k: None)
    rows = va.crawl_wp_jobs({"name": "Medbo", "careers_url": cu})
    assert rows == []


# --- 2026-09-18 crawler review: query-string-only postings, FAQ-shape early return, listing-page
#     URL collapse, and a JSON-LD "url" naming the site root -----------------------------------

def test_wp_job_rows_does_not_collapse_two_postings_distinguished_only_by_query_string(monkeypatch):
    u1 = "https://x.example/uebersicht-aller-stellen/details/?job=1"
    u2 = "https://x.example/uebersicht-aller-stellen/details/?job=2"
    p1 = _R(_jsonld_job("Pflegefachkraft Station A (m/w/d)"), url=u1, ok=True)
    p2 = _R(_jsonld_job("Pflegefachkraft Station B (m/w/d)"), url=u2, ok=True)
    monkeypatch.setattr(va, "get", _router({u1: p1, u2: p2}))
    rows = va._wp_job_rows([u1, u2], {"name": "Klinik", "town": "X"}, "x.example", 10, None)
    titles = sorted(r["payload"]["title"] for r in rows)
    assert titles == ["Pflegefachkraft Station A (m/w/d)", "Pflegefachkraft Station B (m/w/d)"]


def test_faqpage_shape_merges_with_the_normal_walk_instead_of_returning_early(monkeypatch):
    cu = "https://x.example/karriere"
    real_detail = "https://x.example/stellenangebot/pflegefachkraft-notaufnahme"
    faq_json = ('<script type="application/ld+json">{"@type":"FAQPage","mainEntity":['
                '{"@type":"Question","name":"Pflegefachkraft (m/w/d) Station 3",'
                '"acceptedAnswer":{"@type":"Answer","text":"desc"}},'
                '{"@type":"Question","name":"Wie bewerbe ich mich?",'
                '"acceptedAnswer":{"@type":"Answer","text":"per Mail"}}]}</script>')
    cu_page = _R(faq_json + '<a href="%s">Pflegefachkraft Notaufnahme</a>' % real_detail, url=cu, ok=True)
    detail_page = _R(_jsonld_job("Pflegefachkraft Notaufnahme (m/w/d)"), url=real_detail, ok=True)
    monkeypatch.setattr(va, "get", _router({cu: cu_page, real_detail: detail_page}))
    rows = va.crawl_wp_jobs({"name": "Klinik", "careers_url": cu})
    titles = sorted(r["payload"]["title"] for r in rows)
    assert "Wie bewerbe ich mich?" not in titles              # not gender-marked, not a posting
    assert "Pflegefachkraft (m/w/d) Station 3" in titles      # the FAQ-shape posting IS kept
    assert "Pflegefachkraft Notaufnahme (m/w/d)" in titles    # AND the separately-linked real posting
    assert len(titles) == len(set(titles))                    # each kept posting still has its own url


def test_faqpage_job_rows_ignores_a_question_with_no_gender_marker():
    cu_page = _R('<script type="application/ld+json">{"@type":"FAQPage","mainEntity":['
                 '{"@type":"Question","name":"Wie bewerbe ich mich?",'
                 '"acceptedAnswer":{"@type":"Answer","text":"per Mail"}}]}</script>',
                 url="https://x.example/karriere", ok=True)
    rows = va._faqpage_job_rows(cu_page, {"name": "Klinik", "town": "X"}, "x.example")
    assert rows == []


def test_listing_page_only_helpers_give_each_posting_a_distinct_url_without_digits_or_equals():
    faq = _R('<script type="application/ld+json">{"@type":"FAQPage","mainEntity":['
             '{"@type":"Question","name":"Pflegefachkraft (m/w/d) Innere",'
             '"acceptedAnswer":{"text":"a"}},'
             '{"@type":"Question","name":"OTA (m/w/d) Endoskopie","acceptedAnswer":{"text":"b"}}]}</script>',
             url="https://klinik-steger.de/karriere", ok=True)
    accordion = _R(
        '<div class="faqAccCard"><div class="jobHeadmain">Pflegefachkraft (m/w/d)</div>'
        '<div class="faqAccCardBody">a</div></div></div>'
        '<div class="faqAccCard"><div class="jobHeadmain">OTA (m/w/d)</div>'
        '<div class="faqAccCardBody">b</div></div></div>',
        url="https://www.waldhausklinik.de/karriere", ok=True)
    title_only = _R(
        '<div class="et_pb_text_inner"><p>Pflegefachkraft (m/w/d)</p></div>'
        '<div class="et_pb_text_inner"><p>OTA (m/w/d)</p></div>',
        url="https://klinik-bad-trissl.de/karriere", ok=True)
    panel = _R(
        '<div class="panel-title"><a>Pflegefachkraft (VZ/TZ)</a></div>'
        '<div class="panel-body">a</div></div></div>'
        '<div class="panel-title"><a>OTA (VZ/TZ)</a></div>'
        '<div class="panel-body">b</div></div></div>',
        url="https://klinik-wirsberg.de/karriere", ok=True)
    cases = [
        (va._faqpage_job_rows, faq, "klinik-steger.de"),
        (va._faq_accordion_job_rows, accordion, "www.waldhausklinik.de"),
        (va._title_only_job_rows, title_only, "klinik-bad-trissl.de"),
        (va._bootstrap_panel_job_rows, panel, "klinik-wirsberg.de"),
    ]
    for fn, resp, host in cases:
        rows = fn(resp, {"name": "Klinik", "town": "X"}, host)
        assert len(rows) == 2, fn.__name__
        urls = [r["source_url"] for r in rows]
        assert len(set(urls)) == 2, fn.__name__               # no longer collapsed onto one URL
        for u in urls:
            base, frag = u.split("#", 1)
            assert base == resp.url, fn.__name__
            assert not any(ch.isdigit() for ch in frag) and "=" not in frag, fn.__name__


def test_gender_marker_accepts_the_colon_and_asterisk_suffix_form():
    for good in ("Pfleger:in", "Mitarbeiter*in", "Krankenpfleger (m/w/d)"):
        assert va.GENDER.search(good), good
    for bad in ("Berlin", "Sein Beruf", "Auszubildende- in allen Bereichen"):
        assert not va.GENDER.search(bad), bad


def test_faqpage_job_rows_accepts_colon_gender_titles_and_still_drops_the_apply_faq(monkeypatch):
    """klinik-steger.de switched its FAQPage titles from "(m/w/d)" to "Pfleger:in" style -- the
    gender-gate added to keep an application-FAQ question out must not also drop this board's own
    real postings (confirmed live 2026-09-18: 0 rows returned before GENDER learned the colon form)."""
    cu_page = _R('<script type="application/ld+json">{"@type":"FAQPage","mainEntity":['
                 '{"@type":"Question","name":"Gesundheits- und Krankenpfleger:in - fuer die Station",'
                 '"acceptedAnswer":{"text":"a"}},'
                 '{"@type":"Question","name":"Wie bewerbe ich mich?","acceptedAnswer":{"text":"b"}}]}</script>',
                 url="https://klinik-steger.de/stellenangebote/", ok=True)
    rows = va._faqpage_job_rows(cu_page, {"name": "Klinik Steger", "town": "Nuernberg"}, "klinik-steger.de")
    titles = [r["payload"]["title"] for r in rows]
    assert titles == ["Gesundheits- und Krankenpfleger:in - fuer die Station"]


def test_category_filtered_overview_link_is_not_treated_as_a_posting():
    html = ('<a href="/stellenanzeigen/uebersicht.html?kategorie=Pflege">Pflege- und Funktionsdienst</a>'
            '<a href="/stellenanzeigen/pflegefachkraft-station-a/">Pflegefachkraft (m/w/d) Station A</a>')
    pairs = va._job_link_pairs(html, "https://x.example/")
    assert "https://x.example/stellenanzeigen/pflegefachkraft-station-a/" in pairs
    assert "https://x.example/stellenanzeigen/uebersicht.html?kategorie=Pflege" not in pairs


def test_parse_job_page_ignores_json_ld_url_naming_the_site_root_or_a_foreign_host():
    fetched_url = "https://komm-ins-klinikland.de/job/pflegefachkraft-1"
    html_root = _jsonld_job("Pflegefachkraft (m/w/d)", url="https://komm-ins-klinikland.de/")
    j = va.parse_job_page(html_root, fetched_url, "Klinik Kitzinger Land")
    assert j["url"] == fetched_url                             # not the bare site root

    html_foreign = _jsonld_job("Pflegefachkraft (m/w/d)", url="https://other.example/job/1")
    j2 = va.parse_job_page(html_foreign, fetched_url, "Klinik Kitzinger Land")
    assert j2["url"] == fetched_url                            # not a different host

    canonical = "https://komm-ins-klinikland.de/job/canonical-3"
    html_real = _jsonld_job("Pflegefachkraft (m/w/d)", url=canonical)
    j3 = va.parse_job_page(html_real, fetched_url, "Klinik Kitzinger Land")
    assert j3["url"] == canonical                              # a real same-host detail link is kept


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
