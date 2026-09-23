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
    rows = va._wp_job_rows([u1, u2], {"name": "Klinik", "town": "X"}, "x.example", None)
    titles = sorted(r["payload"]["title"] for r in rows)
    assert titles == ["Pflegefachkraft Station A (m/w/d)", "Pflegefachkraft Station B (m/w/d)"]


# --- run-120 recon 2026-09-22: same PATH as the listing, distinguished ONLY by a job-id query -----

def test_same_path_detail_pages_distinguished_only_by_a_query_id_are_not_excluded_as_the_listing(monkeypatch):
    """koenig-ludwig-haus.de shape (confirmed live 2026-09-22): every posting's own url is the
    LISTING's identical path, differing only by ?detID=N -- _listing_page_key is deliberately
    query-blind (see its own docstring), so before this fix every candidate here collapsed onto the
    listing's own not_a_job key and 0 rows ever reached _wp_job_rows (13 real postings lost). The
    live detail page itself also carries no h1/JSON-LD/Bootstrap-classed heading and repeats one
    generic <title> on every single posting -- only the listing anchor's own gender-marked text
    names the real title, same as PDF_LINK_RX's existing anchor-text fallback."""
    cu = "https://x.example/karriere/jobs/index.html"
    u1 = "https://x.example/karriere/jobs/index.html?detID=1"
    u2 = "https://x.example/karriere/jobs/index.html?detID=2"
    cu_page = _R(
        '<a href="%s">Stellenanzeige Pflegefachkraft (w/m/d) Station A</a>'
        '<a href="%s">Stellenanzeige Pflegefachkraft (w/m/d) Station B</a>' % (u1, u2),
        url=cu, ok=True)
    detail_html = '<title>Jobs bei X</title><span class="fancytitle">irrelevant body text</span>'
    p1 = _R(detail_html, url=u1, ok=True)
    p2 = _R(detail_html, url=u2, ok=True)
    monkeypatch.setattr(va, "get", _router({cu: cu_page, u1: p1, u2: p2}))
    rows = va.crawl_wp_jobs({"name": "Klinik", "careers_url": cu})
    titles = sorted(r["payload"]["title"] for r in rows)
    assert titles == ["Pflegefachkraft (w/m/d) Station A", "Pflegefachkraft (w/m/d) Station B"]


def test_career_page_with_no_listing_html_embeds_the_whole_board_as_a_third_party_iframe(monkeypatch):
    """kh-as.de shape (confirmed live 2026-09-22): the career page itself carries no job-looking
    content at all, only <iframe src="https://jobs.maxime-media.de/..."> -- a small vendor with no
    fingerprint anywhere else in this file, but otherwise a plain server-rendered board (real anchor
    links, real JSON-LD per detail page) once you are actually on that host. Before this fix the
    iframe src was never followed at all, so this board (and every other one shaped like it) read 0."""
    cu = "https://x.example/karriere/stellenangebote/"
    iframe_src = "https://jobs.vendor.example/tenant"
    u1 = "https://jobs.vendor.example/tenant/1"
    cu_page = _R('<iframe src="%s" style="width:100%%;border:0"></iframe>' % iframe_src, url=cu, ok=True)
    iframe_page = _R('<a href="%s">Pflegefachkraft (m/w/d) fuer die Kardiologie</a>' % u1, url=iframe_src, ok=True)
    detail_page = _R(_jsonld_job("Pflegefachkraft (m/w/d) fuer die Kardiologie"), url=u1, ok=True)
    monkeypatch.setattr(va, "get", _router({cu: cu_page, iframe_src: iframe_page, u1: detail_page}))
    rows = va.crawl_wp_jobs({"name": "Klinik", "careers_url": cu})
    titles = [r["payload"]["title"] for r in rows]
    assert titles == ["Pflegefachkraft (m/w/d) fuer die Kardiologie"]


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
    accordion2 = _R(
        '<div class="dan-bewerbungen-job-headline"><b>Pflegefachkraft (m/w/d) ()</b></div>'
        '<div class="dan-bewerbungen-job-body"><h3>Wir suchen</h3><p>a</p></div>'
        '<div class="dan-bewerbungen-job-headline"><b>OTA (m/w/d) ()</b></div>'
        '<div class="dan-bewerbungen-job-body"><h3>Wir suchen</h3><p>b</p></div>',
        url="https://www.kreisklinik-woerth.de/stellenangebote/", ok=True)
    toggle = _R(
        '<a class="elementor-toggle-title" tabindex="0">Pflegefachkraft (m/w/d)</a>'
        '<div class="elementor-tab-content elementor-clearfix">a</div></div>'
        '<a class="elementor-toggle-title" tabindex="0">OTA (m/w/d)</a>'
        '<div class="elementor-tab-content elementor-clearfix">b</div></div>',
        url="https://www.spezialklinik-neukirchen.de/ueber-uns/stellenangebote/", ok=True)
    cases = [
        (va._faqpage_job_rows, faq, "klinik-steger.de"),
        (va._faq_accordion_job_rows, accordion, "www.waldhausklinik.de"),
        (va._title_only_job_rows, title_only, "klinik-bad-trissl.de"),
        (va._bootstrap_panel_job_rows, panel, "klinik-wirsberg.de"),
        (va._dan_bewerbungen_job_rows, accordion2, "www.kreisklinik-woerth.de"),
        (va._elementor_toggle_job_rows, toggle, "www.spezialklinik-neukirchen.de"),
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


def test_dan_bewerbungen_accordion_strips_the_templates_own_trailing_empty_parens(monkeypatch):
    """kreisklinik-woerth.de shape (confirmed live 2026-09-22): a legacy <font>-tag page whose
    postings sit in a "dan-bewerbungen-job-headline"/"-job-body" accordion pair, title repeated a
    second time (with the real Wir-suchen/Wir-sind/... body) inside the body div -- the split on the
    headline regex alone must not also re-match that inner duplicate, and every title in this
    plugin's own template carries a trailing "()" placeholder that must not leak into the row."""
    cu = "https://www.kreisklinik-woerth.de/stellenangebote/"
    html = (
        '<div class="dan-bewerbungen-job-headline dan-bewerbungen-job-headline-closed">'
        '<b>Gesundheits- und Krankenpfleger (m/w/d) ()</b></div>'
        '<div class="dan-bewerbungen-job-body dan-bewerbungen-job-body-closed">'
        '<h3>Wir suchen</h3><p><b>Gesundheits- und Krankenpfleger </b>(m/w/d) </p>'
        '<h3>Wir sind</h3><p>Eine Kreisklinik.</p>'
        '<p><a href="/online-bewerbung?jobid=399">Hier gehts zum Online-Bewerbungsformular</a></p></div>')
    monkeypatch.setattr(va, "get", _router({cu: _R(html, url=cu, ok=True)}))
    rows = va.crawl_wp_jobs({"name": "Klinik", "careers_url": cu})
    titles = [r["payload"]["title"] for r in rows]
    assert titles == ["Gesundheits- und Krankenpfleger (m/w/d)"]  # not "...(m/w/d) ()"


def test_elementor_toggle_accordion_skips_ungendered_titles_and_keeps_the_real_posting(monkeypatch):
    """spezialklinik-neukirchen.de shape (confirmed live 2026-09-22): an Elementor Toggle widget
    (a DIFFERENT Elementor widget than INLINE_HEADING_SITES' Heading one -- own class name, and the
    toggle title itself is a JS-only <a> with no href to find nearby). Most of this board's own
    toggle entries carry no gender marker at all (reception/admin role, a doctor-leadership role, a
    flat "we do NOT train for X" non-opening) -- only the one real gendered posting should survive."""
    cu = "https://www.spezialklinik-neukirchen.de/ueber-uns/stellenangebote/"
    html = (
        '<a class="elementor-toggle-title" tabindex="0">Patientenverwaltung / Empfang</a>'
        '<div class="elementor-tab-content elementor-clearfix"><p>Ungendered admin role.</p></div></div>'
        '<a class="elementor-toggle-title" tabindex="0">Examinierte/n Krankenschwester/- pfleger (m/w/d)</a>'
        '<div class="elementor-tab-content elementor-clearfix"><p>Ab sofort suchen wir fuer unsere Station.</p></div></div>')
    monkeypatch.setattr(va, "get", _router({cu: _R(html, url=cu, ok=True)}))
    rows = va.crawl_wp_jobs({"name": "Klinik", "careers_url": cu})
    titles = [r["payload"]["title"] for r in rows]
    assert titles == ["Examinierte/n Krankenschwester/- pfleger (m/w/d)"]
    assert "Station" in rows[0]["payload"]["description"]


def test_typo3_eid_dumpfile_pdf_link_uses_anchor_text_like_a_suffixed_pdf(monkeypatch):
    """panorama-fachklinik.de shape (confirmed live 2026-09-22): TYPO3's own eID=dumpFile download
    handler serves a PDF flyer with no ".pdf" anywhere in the URL at all -- the same "anchor text is
    the only real title" shape PDF_LINK_RX already handles for a literal .pdf suffix (augenklinik-
    muenchen.de), just a different URL shape. The PDF url is deliberately NOT in the router mapping:
    a correct fix never fetches it at all (same as the .pdf-suffixed case) -- if this ever regressed
    to trying to fetch it, the router's 404-shaped default response would silently drop the row
    instead of raising, so the row's presence is what proves the no-fetch path ran."""
    cu = "https://x.example/karriere/jobs/"
    pdf_url = "https://x.example/index.php?eID=dumpFile&t=f&f=2336&token=abc123"
    cu_page = _R('<a href="%s">Assistenzarzt (m/w/d) fuer psychosomatische Medizin</a>' % pdf_url, url=cu, ok=True)
    monkeypatch.setattr(va, "get", _router({cu: cu_page}))
    rows = va.crawl_wp_jobs({"name": "Klinik", "careers_url": cu})
    titles = [r["payload"]["title"] for r in rows]
    assert titles == ["Assistenzarzt (m/w/d) fuer psychosomatische Medizin"]


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


def test_job_link_pairs_reads_a_gender_marker_slugified_into_the_url_itself():
    """clinicum-stgeorg.de shape (confirmed live 2026-09-22): detail links are plain post slugs with
    no job/stellen/karriere keyword in the path (JOB_PATH misses) AND the anchor's own visible text
    is a generic "Zum Jobangebot: <title>" with no gender marker either (GENDER-on-text misses) --
    the ONLY signal is "(m/w/d)" slugified into the URL itself, e.g. "...-m-w-d-vollzeit-110488"."""
    html = ('<a href="/karriere/gesundheits-und-krankenpfleger-m-w-d-vollzeit-110488">'
            'Zum Jobangebot: Gesundheits- und Krankenpfleger</a>'
            '<a href="/karriere/impressum">Impressum</a>')
    pairs = va._job_link_pairs(html, "https://x.example/")
    assert "https://x.example/karriere/gesundheits-und-krankenpfleger-m-w-d-vollzeit-110488" in pairs
    assert "https://x.example/karriere/impressum" not in pairs


def test_stgeorg_shape_end_to_end_reads_the_real_detail_page_title(monkeypatch):
    cu = "https://x.example/karriere"
    detail = "https://x.example/karriere/gesundheits-und-krankenpfleger-m-w-d-vollzeit-110488"
    cu_page = _R('<a href="%s"><span class="visually-hidden">Zum Jobangebot: '
                 'Gesundheits- und Krankenpfleger</span></a>' % detail, url=cu, ok=True)
    detail_page = _R('<h1 class="hyphens h2">Gesundheits- und Krankenpfleger (m/w/d)</h1>', url=detail, ok=True)
    monkeypatch.setattr(va, "get", _router({cu: cu_page, detail: detail_page}))
    rows = va.crawl_wp_jobs({"name": "Klinik", "careers_url": cu})
    titles = [r["payload"]["title"] for r in rows]
    assert titles == ["Gesundheits- und Krankenpfleger (m/w/d)"]


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


# --- title: a heading styled with a Bootstrap h1/h2/h3 class is still a heading ------------------

def test_parse_job_page_reads_a_class_styled_heading_when_no_real_h_tag_exists():
    """karriere.klinikverbund-allgaeu.de (TASK-49, 1048 beds across 6 clinics) renders every detail
    page's real title as <strong class="h1 ..."> and has no <h1>/<h2>/<h3> at all -- so the board's
    own generic page <title> became the title of all 82 of its postings, one indistinguishable
    non-title for the whole board."""
    html = ("<html><head><title> Karriere Detail - Klinikverbund Allgäu</title></head><body>"
            '<div class="tx-sd-jobs-haufe">'
            '<strong class="h1 font-weight-bolder mt-5">SAPV Pflegefachkraft in Teilzeit (m/w/d)</strong>'
            "</div></body></html>")
    j = va.parse_job_page(html, "https://karriere.klinikverbund-allgaeu.de/karriere-detail/Kempten/x/2596",
                          "Klinikverbund Allgäu")
    assert j["title"] == "SAPV Pflegefachkraft in Teilzeit (m/w/d)"


def test_parse_job_page_still_prefers_a_real_heading_over_a_class_styled_one():
    html = ("<html><head><title>Board</title></head><body>"
            "<h2>Pflegefachkraft (m/w/d) Intensiv</h2>"
            '<p class="h3">Praxisanleiter (m/w/d)</p></body></html>')
    j = va.parse_job_page(html, "https://x/stellen/a", "Klinikum")
    assert j["title"] == "Pflegefachkraft (m/w/d) Intensiv"


# --- title: HubSpot "Stellenanzeige | <real title>" -- generic label FIRST (TASK-52) -------------

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_samples")


def _mkh_sample():
    with open(os.path.join(FIXTURES, "meinkrankenhaus2030_stellenanzeige_sample.html"), encoding="utf-8") as f:
        return f.read()


def test_hubspot_board_title_is_the_pipe_segment_that_carries_the_gender_marker():
    """meinkrankenhaus2030.de (Krankenhaus Weilheim 19002 / Schongau 19001, shared board, 17 live
    postings): no JSON-LD, no heading tag of any level, so the page <title> is the only title there
    is -- and this HubSpot template writes it as "Stellenanzeige | <real title>", the generic label
    in segment 0. Taking segment 0 gave every one of the 17 postings the literal title
    "Stellenanzeige", which classify_role then correctly rejected as non-nursing: 0 rows kept."""
    j = va.parse_job_page(_mkh_sample(),
                          "https://www.meinkrankenhaus2030.de/stellenanzeige-operations-technischen-assistent-w/m/d-in-vollzeit",
                          "Krankenhaus Schongau")
    assert j["title"] == "Operations-Technischen-Assistent / OP-Pflegefachkräfte (w/m/d) in Vollzeit/Teilzeit"
    from pflege_jobs.classify import classify_role
    assert classify_role(j["title"], "", "")[0] == "pflegefachkraft"


def test_hubspot_board_yields_its_nursing_posting_end_to_end(monkeypatch):
    cu = "https://www.meinkrankenhaus2030.de/karriere/stellenboerse"
    detail = "https://www.meinkrankenhaus2030.de/stellenanzeige-operations-technischen-assistent-w/m/d-in-vollzeit"
    mapping = {cu: _R('<a href="%s?hsLang=de-de">Stellenanzeige</a>' % detail, url=cu, ok=True),
               detail + "?hsLang=de-de": _R(_mkh_sample(), url=detail, ok=True)}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_wp_jobs({"name": "Krankenhaus Schongau", "town": "Schongau", "careers_url": cu})
    assert [r["payload"]["title"] for r in rows] == \
        ["Operations-Technischen-Assistent / OP-Pflegefachkräfte (w/m/d) in Vollzeit/Teilzeit"]


def test_pipe_title_without_any_gender_marker_still_takes_segment_zero():
    """The segment preference must not become "always take the last segment": the ordinary
    "<real title> | SiteName" convention is still the common case."""
    html = "<html><head><title>Pflegedienstleitung | Klinikum Musterstadt</title></head><body></body></html>"
    assert va.parse_job_page(html, "https://x/stellen/a", "Klinikum")["title"] == "Pflegedienstleitung"


# --- recursive listing-page fallback must not drop the outer discovery's own anchor titles ------

def test_recursive_listing_candidates_inherit_the_already_known_anchor_titles(monkeypatch):
    """koenig-ludwig-haus.de (clinic 66305): the career page's own detail pages carry no h1/JSON-LD
    at all, only a shared generic <title> -- so when a page LOOKS like a listing (recurses via
    _job_link_pairs, len(sub)>1) the only real title left for its own sub-candidates is the anchor
    text discovered ON that page. Before the fix, the recursive _wp_job_rows call dropped `titles`
    entirely, so every recursive candidate fell through to the shared generic title instead
    (confirmed live 2026-09-22: 7 of 10 real postings lost or mistitled this way)."""
    cu = "https://x.example/karriere/"
    division = "https://x.example/karriere/stellen/uebersicht"
    b = "https://x.example/karriere/stellen/b"
    c = "https://x.example/karriere/stellen/c"
    cu_page = _R('<a href="%s">Alle Stellen</a>' % division, url=cu, ok=True)
    division_page = _R(
        '<title>Job Overview</title>'
        '<a href="%s">Pflegefachkraft (m/w/d)</a>'
        '<a href="%s">Examinierte Pflegekraft (m/w/d)</a>' % (b, c),
        url=division, ok=True)
    # Both detail pages repeat the SAME generic, ungendered <title> -- no other heading anywhere.
    detail_b = _R('<title>Stellenangebote</title>', url=b, ok=True)
    detail_c = _R('<title>Stellenangebote</title>', url=c, ok=True)
    mapping = {cu: cu_page, division: division_page, b: detail_b, c: detail_c}
    monkeypatch.setattr(va, "get", _router(mapping))
    rows = va.crawl_wp_jobs({"name": "Klinik X", "careers_url": cu})
    titles = sorted(r["payload"]["title"] for r in rows)
    assert titles == ["Examinierte Pflegekraft (m/w/d)", "Pflegefachkraft (m/w/d)"]  # not "Stellenangebote" x2


def test_two_path_aliases_sharing_one_query_id_collapse_to_one_row(monkeypatch):
    """koenig-ludwig-haus.de serves every posting under BOTH .../index.html?detID=N and
    .../NNNNN.Stellenanzeigen.html?detID=N -- two different paths, the same query id. Confirmed live
    2026-09-22: _fetch_dedupe_key keeps the path, so this doubled every row."""
    alias_a = "https://x.example/karriere/index.html?detID=7"
    alias_b = "https://x.example/karriere/9999.Stellenanzeigen.html?detID=7"
    # Each alias answers as ITSELF (no redirect) -- the existing final_key-collapse (line ~849,
    # for a slug that redirects to a shared landing page) must not be what makes this test pass.
    detail_a = _R(_jsonld_job("Pflegefachkraft (m/w/d)"), url=alias_a, ok=True)
    detail_b = _R(_jsonld_job("Pflegefachkraft (m/w/d)"), url=alias_b, ok=True)
    monkeypatch.setattr(va, "get", _router({alias_a: detail_a, alias_b: detail_b}))
    rows = va._wp_job_rows([alias_a, alias_b], {"name": "Klinik X", "town": "X"}, "x.example", None)
    assert len(rows) == 1


# --- a standard legal-notice page must never be accepted as a posting ---------------------------

def test_legal_notice_pages_are_never_accepted_via_the_ungendered_single_candidate_fallback(monkeypatch):
    """psychiatrie-werneck.de (clinic 66205): the meta-refresh-following fix (this session, in the
    shared get()) newly lets a site-wide "Impressum"/"Barrierefreiheitserklaerung" link resolve all
    the way to its target instead of bouncing -- confirmed live 2026-09-22, both landed here via the
    len(sub)<=1 ungendered fallback and were stored as fake postings. Calls _wp_job_rows directly
    (not crawl_wp_jobs' own discovery, which a bare "Impressum" link never even survives) -- this is
    a defense at the acceptance point itself, for whatever board-specific discovery route reaches it."""
    legal = "https://x.example/impressum"
    legal_page = _R('<title>Impressum</title>', url=legal, ok=True)
    monkeypatch.setattr(va, "get", _router({legal: legal_page}))
    rows = va._wp_job_rows([legal], {"name": "Klinik X", "town": "X"}, "x.example", None)
    assert rows == []


# --- TASK-118: no structured location field at all, only plain body prose -----------------------

def test_wp_job_rows_reads_a_standort_mention_when_the_page_has_no_jsonld_location(monkeypatch):
    """meinkrankenhaus2030.de (clinics 19001 Schongau / 19002 Weilheim, shared board, no JSON-LD at
    all): the real work site is only ever stated as "...am Standort Weilheim..." in plain body
    prose. Confirmed live 2026-09-23 against the real page."""
    url = "https://x.example/stellenanzeige-ota"
    page = _R("<h1>Operations-Technischen-Assistent (m/w/d)</h1>"
              "<p>Für unsere OP-Abteilung am Standort Weilheim suchen wir Verstärkung.</p>", url=url, ok=True)
    monkeypatch.setattr(va, "get", _router({url: page}))
    rows = va._wp_job_rows([url], {"name": "Krankenhaus Schongau", "town": "Schongau"}, "x.example", None,
                           towns={"schongau", "weilheim"})
    assert rows[0]["payload"]["loc"][0]["city"] == "Weilheim"
    assert rows[0]["payload"].get("city_source") is None


def test_wp_job_rows_falls_back_to_the_seed_town_when_no_standort_is_stated(monkeypatch):
    url = "https://x.example/stellenanzeige-other"
    page = _R("<h1>Pflegefachkraft (m/w/d)</h1><p>Wir suchen Verstärkung für unser Team.</p>", url=url, ok=True)
    monkeypatch.setattr(va, "get", _router({url: page}))
    rows = va._wp_job_rows([url], {"name": "Krankenhaus Schongau", "town": "Schongau"}, "x.example", None,
                           towns={"schongau", "weilheim"})
    assert rows[0]["payload"]["loc"][0]["city"] == "Schongau"
    assert rows[0]["payload"]["city_source"] == "seed"
