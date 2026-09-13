"""career_crawl.Crawler: section-first BFS gate. Pure in-process fake fetch, no network.

Contract under test (see pflege_jobs/section.py + TASK-56 / commit 0ee9828, which rewrote this):
  - a confident nursing-section link on the seed page -> fetch that subtree first (depth<=2),
    threading classify_role's nursing_section_confirmed signal into every job found there;
  - the full board-wide walk ALWAYS runs too and is merged in, deduped by URL -- an earlier version
    walked ONLY the subtree and returned early, which silently dropped most of a real board (93 of
    102 postings, Klinikverbund Allgaeu, 2026-09-11) whenever the matched section nav link was
    narrower than the real nursing section; never do that again;
  - on a duplicate URL both walks reach, the section walk's row wins (it carries the confirmed
    signal) -- the full walk only tops up jobs the section subtree missed;
  - no confident section link at all -> full board-wide walk, unchanged from before this feature.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources.career_crawl import Crawler  # noqa: E402

JOBPOSTING_TMPL = """<html><head>
<script type="application/ld+json">
{{
  "@context": "https://schema.org", "@type": "JobPosting",
  "title": "{title}",
  "description": "Wir suchen Verstaerkung.",
  "datePosted": "2026-01-01",
  "jobLocation": {{"@type": "Place", "address": {{"@type": "PostalAddress",
    "addressLocality": "Muenchen", "postalCode": "80331", "addressRegion": "Bayern"}}}}
}}
</script></head><body><h1>{title}</h1></body></html>"""


class R:
    def __init__(self, text, url):
        self.text, self.url = text, url


def _crawler(fetch_map, **kw):
    cr = Crawler(towns={"muenchen"}, per_site_pages=50, list_pages=12, sleep=0, log=lambda *a, **k: None, **kw)
    calls = []
    def _fetch(url, _calls=calls, _map=fetch_map):
        _calls.append(url)
        return _map.get(url)
    cr.fetch = _fetch
    cr.calls = calls
    return cr


def test_section_first_threads_nursing_section_confirmed_into_classify_role():
    # career_crawl's own section-first path was tested live 2026-09-06 on 2 real umantis boards and
    # never actually engaged (no nursing nav rendered on either) -- so there is no real observed
    # classification gap for this path specifically. This test only proves the wiring is mechanically
    # correct: a title with no pflege_gate token (reusing the real dvinci survey example "Advanced
    # Practice Nurses (m/w/d)", which genuinely failed only that gate on a confirmed nursing board)
    # is classified as nursing (role_class "apn_experte") when reached through a confirmed section
    # subtree -- outside a confirmed section the same title is dropped as nicht_pflege/no_pflege_token
    # (see tests/test_classify_section.py, which covers that gate behaviour on the real title/dept
    # pair directly).
    seed_url = "https://example-klinik.de/karriere/"
    section_url = "https://example-klinik.de/karriere/pflege/"
    job_url = "https://example-klinik.de/karriere/pflege/job-1"
    seed_html = '<a href="/karriere/pflege/">Pflegedienst</a>'
    section_html = '<a href="/karriere/pflege/job-1">Advanced Practice Nurses (m/w/d)</a>'
    job_html = JOBPOSTING_TMPL.format(title="Advanced Practice Nurses (m/w/d)")

    fetch_map = {
        seed_url: R(seed_html, seed_url),
        section_url: R(section_html, section_url),
        job_url: R(job_html, job_url),
    }
    cr = _crawler(fetch_map)
    seed = {"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"}
    rows, stats = cr.crawl(seed)

    assert stats["section_first"] is True
    assert len(rows) == 1
    assert rows[0]["role_class"] == "apn_experte"
    assert rows[0]["role_rule"] == "apn_experte:advanced practice"


def test_section_first_still_tops_up_with_the_full_board_walk():
    """TASK-56 (commit 0ee9828): an earlier version of this crawler returned as soon as the matched
    section subtree had any rows, and never touched the rest of the board. That silently dropped 93 of
    102 real postings on a live board (Klinikverbund Allgaeu, 2026-09-11) once the matched nav link was
    narrower than the real nursing section. So the full board-wide walk always runs too -- a job living
    outside the confirmed subtree must still come back, alongside the one found through it."""
    seed_url = "https://example-klinik.de/karriere/"
    section_url = "https://example-klinik.de/karriere/pflege/"
    job_url = "https://example-klinik.de/karriere/pflege/job-1"
    other_board_url = "https://example-klinik.de/karriere/alle-stellen/"
    other_job_url = "https://example-klinik.de/karriere/alle-stellen/facharzt"

    seed_html = (
        '<a href="/karriere/pflege/">Pflegedienst</a>'
        '<a href="/karriere/alle-stellen/">Alle Stellenangebote</a>'
    )
    section_html = '<a href="/karriere/pflege/job-1">Pflegefachkraft (m/w/d) Station 3</a>'
    job_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Station 3")
    other_board_html = '<a href="/karriere/alle-stellen/facharzt">Facharzt (m/w/d) Gefaesschirurgie</a>'
    other_job_html = JOBPOSTING_TMPL.format(title="Facharzt (m/w/d) Gefaesschirurgie")

    fetch_map = {
        seed_url: R(seed_html, seed_url),
        section_url: R(section_html, section_url),
        job_url: R(job_html, job_url),
        other_board_url: R(other_board_html, other_board_url),
        other_job_url: R(other_job_html, other_job_url),
    }
    cr = _crawler(fetch_map)
    seed = {"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"}
    rows, stats = cr.crawl(seed)

    assert stats["section_first"] is True
    # both the nursing job (through the confirmed subtree) and the one living outside it come back
    assert {r["title"] for r in rows} == {"Pflegefachkraft (m/w/d) Station 3", "Facharzt (m/w/d) Gefaesschirurgie"}
    assert other_board_url in cr.calls and other_job_url in cr.calls


def test_section_first_falls_back_to_full_walk_when_subtree_is_empty():
    seed_url = "https://example-klinik.de/karriere/"
    section_url = "https://example-klinik.de/karriere/pflege/"
    job_url = "https://example-klinik.de/karriere/job-1"

    seed_html = (
        '<a href="/karriere/pflege/">Pflegedienst</a>'
        '<a href="/karriere/job-1">Pflegefachkraft (m/w/d) Anaesthesie</a>'
    )
    # the "Pflegedienst" link is a dead/empty category page -- no job links, no postings
    section_html = "<html><body>Aktuell keine offenen Stellen in dieser Kategorie.</body></html>"
    job_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Anaesthesie")

    fetch_map = {
        seed_url: R(seed_html, seed_url),
        section_url: R(section_html, section_url),
        job_url: R(job_html, job_url),
    }
    cr = _crawler(fetch_map)
    seed = {"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"}
    rows, stats = cr.crawl(seed)

    # section_first reports whether a confident section link was found on the seed page at all
    # (TASK-56) -- it stays True even when that subtree turns out empty; the full board-wide walk,
    # which always runs, is what actually recovers the job below.
    assert stats["section_first"] is True
    assert len(rows) == 1
    assert rows[0]["title"] == "Pflegefachkraft (m/w/d) Anaesthesie"


def test_no_section_link_runs_full_walk_unchanged():
    seed_url = "https://example-klinik.de/karriere/"
    job_url = "https://example-klinik.de/karriere/job-1"
    seed_html = '<a href="/karriere/job-1">Pflegefachkraft (m/w/d) Intensivstation</a>'
    job_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Intensivstation")

    fetch_map = {seed_url: R(seed_html, seed_url), job_url: R(job_html, job_url)}
    cr = _crawler(fetch_map)
    seed = {"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"}
    rows, stats = cr.crawl(seed)

    assert stats["section_first"] is False
    assert len(rows) == 1


def test_section_first_recognises_option_data_url_category_picker():
    # Real pattern seen on bespoke WP career boards (e.g. muenchen-klinik.de/jobs/): a Berufsgruppe
    # <select> whose <option> carries the target listing URL in a data-url attribute, not an <a href>.
    seed_url = "https://example-klinik.de/jobs/"
    section_url = "https://example-klinik.de/jobs/pflege/"
    job_url = "https://example-klinik.de/jobs/pflege/job-1"
    seed_html = (
        '<select><option value="1" data-url="https://example-klinik.de/jobs/arzt/">Ärzte</option>'
        '<option value="2" data-url="https://example-klinik.de/jobs/pflege/">Pflegedienst</option></select>'
    )
    section_html = '<a href="/jobs/pflege/job-1">Pflegefachkraft (m/w/d)</a>'
    job_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d)")

    fetch_map = {seed_url: R(seed_html, seed_url), section_url: R(section_html, section_url), job_url: R(job_html, job_url)}
    cr = _crawler(fetch_map)
    seed = {"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"}
    rows, stats = cr.crawl(seed)

    assert stats["section_first"] is True
    assert len(rows) == 1


def test_section_link_ignores_individual_job_postings_that_merely_mention_pflege():
    # A job title like "Pflegefachkraft (m/w/d)" must not itself be mistaken for a nursing *section*
    # nav link -- pick_nursing_link's caller must only consider non-job nav candidates.
    seed_url = "https://example-klinik.de/karriere/"
    job_url = "https://example-klinik.de/karriere/pflegefachkraft-jobid-42"
    seed_html = '<a href="/karriere/pflegefachkraft-jobid-42">Pflegefachkraft (m/w/d)</a>'
    job_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d)")

    fetch_map = {seed_url: R(seed_html, seed_url), job_url: R(job_html, job_url)}
    cr = _crawler(fetch_map)
    seed = {"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"}
    rows, stats = cr.crawl(seed)

    assert stats["section_first"] is False
    assert len(rows) == 1
