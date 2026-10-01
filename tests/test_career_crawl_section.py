"""career_crawl.Crawler: section-first BFS gate. Pure in-process fake fetch, no network.

Contract under test (see pflege_jobs/section.py + the task note in ats_seeds/career_crawl):
  - a confident nursing-section link on the seed page -> walk ONLY that subtree first (depth<=2);
  - if that subtree yields zero jobs -> fall back to the original full board-wide walk;
  - no confident section link at all -> full board-wide walk, unchanged from before this feature.
"""
import os
import sys

import pytest

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
    kw.setdefault("per_site_pages", 50)
    kw.setdefault("list_pages", 12)
    kw.setdefault("sleep", 0)
    kw.setdefault("log", lambda *a, **k: None)
    cr = Crawler(towns={"muenchen"}, **kw)
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


def test_section_first_scopes_walk_to_the_nursing_subtree():
    seed_url = "https://example-klinik.de/karriere/"
    section_url = "https://example-klinik.de/karriere/pflege/"
    job_url = "https://example-klinik.de/karriere/pflege/job-1"
    # A full-board listing link is also present on the seed page; if section-first is working, the
    # crawler must never fetch it (it would otherwise surface a non-nursing job into `job_links`).
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
    # Since 0ee9828 the nursing section is fetched FIRST but no longer ends the crawl: stopping there
    # silently dropped 2 real nursing postings on Klinikum Nuernberg that were filed under another
    # "Jobwelt". Both the section job and the rest of the board are expected now...
    assert {r["title"] for r in rows} == {"Pflegefachkraft (m/w/d) Station 3", "Facharzt (m/w/d) Gefaesschirurgie"}
    assert other_board_url in cr.calls
    # ...and the section row still wins the merge, so its confirmed-nursing signal survives.
    assert rows[0]["title"] == "Pflegefachkraft (m/w/d) Station 3"


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

    # the flag records that a nursing section WAS found and tried; the empty subtree simply
    # contributed nothing and the full walk supplied the posting
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


def test_crawl_urls_still_reads_pagination_the_page_budget_can_afford_after_dead_list_urls():
    """TASK-14: the `len(seen_lists) + len(list_q) >= list_budget * 2` queue ceiling dropped
    candidate list pages for good. seen_lists counts every url POPPED, including ones whose fetch
    failed -- and a failed fetch never raises list_pages. So a board with dead list urls filled the
    queue ceiling while list_pages was still far under list_budget, and lost pagination it had the
    budget to read (live: ANregiomed, list_pages 102 of 500, truncated=True with nothing else able
    to set it). Here: 5 dead list urls + one real page 2 carrying the only job on the board."""
    seed_url = "https://example-klinik.de/karriere/"
    page2_url = "https://example-klinik.de/karriere/?page=2"
    job_url = "https://example-klinik.de/karriere/job/1"
    # list_pages=3 -> the old ceiling was 6, reached at seed + 5 queued, so ?page=2 (emitted last)
    # was dropped. Only 1 of the 3 affordable list-page fetches had actually been spent by then.
    dead = "".join('<a href="/karriere/tot-%d?seite=1">weitere Seite</a>' % i for i in range(5))
    seed_html = dead + '<a href="/karriere/?page=2">weiter</a>'
    page2_html = '<a href="/karriere/job/1">Pflegefachkraft (m/w/d) Station A</a>'
    fetch_map = {seed_url: R(seed_html, seed_url), page2_url: R(page2_html, page2_url),
                 job_url: R(JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Station A"), job_url)}
    cr = _crawler(fetch_map, list_pages=3)

    rows, stats = cr._crawl_urls({"name": "X", "kez": "1", "career": seed_url}, {"example-klinik.de"}, [seed_url], [])

    assert page2_url in cr.calls                  # the dropped candidate is fetched now
    assert len(rows) == 1                         # ...and its job reaches the result
    assert stats["truncated"] is False            # nothing was left unread


def test_section_first_merge_keeps_the_sub_walks_truncated_flag():
    """TASK-72 AC#3: crawl() used to merge only the page/job counts from the section-first sub-walk
    into the final stats, silently dropping ITS OWN truncated flag -- a board whose confirmed
    nursing section alone needed more list-page hops than list_budget allowed reported a clean,
    complete walk. The seed's own nav link is an <option data-url> (not a plain <a href>), so the
    full board-wide walk's own link scan (which only reads <a href>) never re-discovers the section
    itself and this test stays isolated to the sub-walk's own truncation."""
    seed_url = "https://example-klinik.de/karriere/"
    section_url = "https://example-klinik.de/karriere/pflege/"
    section_page2_url = "https://example-klinik.de/karriere/pflege/?page=2"
    job_url = "https://example-klinik.de/karriere/pflege/job-1"
    seed_html = '<select><option value="1" data-url="%s">Pflegedienst</option></select>' % section_url
    section_html = ('<a href="/karriere/pflege/job-1">Pflegefachkraft (m/w/d) Station A</a>'
                     '<a href="/karriere/pflege/?page=2">weiter</a>')
    job_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Station A")
    fetch_map = {seed_url: R(seed_html, seed_url), section_url: R(section_html, section_url), job_url: R(job_html, job_url)}
    # list_pages=1: the section subtree needs a 2nd list-page hop (page 2) that this can't afford;
    # the full board-wide walk needs only the one seed page (no <a href> on it at all) and stays clean.
    cr = _crawler(fetch_map, list_pages=1)
    seed = {"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"}

    rows, stats = cr.crawl(seed)

    assert stats["section_first"] is True
    assert len(rows) == 1
    assert section_page2_url not in cr.calls      # page 2 never fetched -- list_budget hit inside the section subtree
    assert stats["truncated"] is True             # ...and that must still be visible after the merge


def test_section_first_merge_keeps_truncated_even_when_the_subtree_found_nothing():
    """TASK-72 AC#3, the narrower edge the first fix missed: when the section subtree is BOTH
    truncated and empty, the merge used to skip it entirely (gated on section_rows being non-empty),
    so a section that hit its own list_budget before reaching any job page still reported clean."""
    seed_url = "https://example-klinik.de/karriere/"
    section_url = "https://example-klinik.de/karriere/pflege/"
    section_page2_url = "https://example-klinik.de/karriere/pflege/?page=2"
    job_url = "https://example-klinik.de/karriere/job-1"
    seed_html = ('<select><option value="1" data-url="%s">Pflegedienst</option></select>'
                 '<a href="/karriere/job-1">Pflegefachkraft (m/w/d) X</a>') % section_url
    # section page 1 has a next-page link but no job link at all -- section_rows ends up empty
    section_html = '<a href="/karriere/pflege/?page=2">weiter</a>'
    job_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) X")
    fetch_map = {seed_url: R(seed_html, seed_url), section_url: R(section_html, section_url), job_url: R(job_html, job_url)}
    cr = _crawler(fetch_map, list_pages=1)
    seed = {"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"}

    rows, stats = cr.crawl(seed)

    assert stats["section_first"] is True
    assert len(rows) == 1                         # the full walk's own job, found via the plain seed-page link
    assert section_page2_url not in cr.calls       # section subtree truncated before reaching page 2
    assert stats["truncated"] is True


# --- TASK-84 AC1: a link is only a posting on its OWN posting-shaped signal ----------------------

def test_category_link_with_no_gender_marker_is_queued_not_emitted_as_a_posting():
    """St. Josef Regensburg/36202 (confirmed live 2026-09-21): a division/category link
    ('Pflegedienst', href matching JOB_HREF via '/stellenangebot' but no gender marker of its own)
    used to be emitted straight into job_links and, since job_links is a dead end (its own links are
    never explored), the BFS never reached '/alle-stellenangebote' one hop behind it -- 9 of 14 real
    vacancies were never seen. It must now be queued as a list page instead, so the real listing
    behind it is still reached."""
    seed_url = "https://example-klinik.de/karriere/"
    category_url = "https://example-klinik.de/stellenangebote/pflege/"
    listing_url = "https://example-klinik.de/alle-stellenangebote"
    job_url = "https://example-klinik.de/job/1"
    seed_html = '<a href="/stellenangebote/pflege/">Pflegedienst</a>'
    # category page itself carries no job -- just the real listing one hop further in
    category_html = '<a href="/alle-stellenangebote">Alle Stellenangebote</a>'
    listing_html = '<a href="/job/1">Pflegefachkraft (m/w/d) Station A</a>'
    job_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Station A")
    fetch_map = {seed_url: R(seed_html, seed_url), category_url: R(category_html, category_url),
                 listing_url: R(listing_html, listing_url), job_url: R(job_html, job_url)}
    cr = _crawler(fetch_map)

    rows, stats = cr._crawl_urls({"name": "X", "kez": "1", "career": seed_url}, {"example-klinik.de"}, [seed_url], [])

    assert category_url in cr.calls and listing_url in cr.calls  # both hops reached
    assert len(rows) == 1 and rows[0]["title"] == "Pflegefachkraft (m/w/d) Station A"
    assert "Pflegedienst" not in {r["title"] for r in rows}      # the category link itself never becomes a row


def test_junk_nav_link_matching_job_href_produces_no_row_when_its_page_is_not_a_posting():
    """The other half of the same bug (12 junk rows at 56101): 'Ansprechpartner', href matching
    JOB_HREF, no gender marker -- must not become a posting just because a plain contact page happens
    to sit under a job-ish path and has no further job-shaped content of its own."""
    seed_url = "https://example-klinik.de/karriere/"
    contact_url = "https://example-klinik.de/karriere/jobs/ansprechpartner"
    seed_html = '<a href="/karriere/jobs/ansprechpartner">Ansprechpartner</a>'
    # carries "bewerb" (Crawler._heuristic's own "is this even a candidate page" gate) so this test
    # actually exercises the posting-shaped-signal requirement, not just _heuristic's unrelated gate
    contact_html = ("<html><body><h1>Ansprechpartner</h1>"
                     "<p>Frau Muster, Personalabteilung. Bewerbungen bitte per Post.</p></body></html>")
    fetch_map = {seed_url: R(seed_html, seed_url), contact_url: R(contact_html, contact_url)}
    cr = _crawler(fetch_map)

    rows, stats = cr._crawl_urls({"name": "X", "kez": "1", "career": seed_url}, {"example-klinik.de"}, [seed_url], [])

    assert contact_url in cr.calls   # queued and fetched (as a list-page candidate)...
    assert rows == []                # ...but produced no posting: no gender marker, no JobPosting JSON-LD


def test_jsonld_jobposting_on_a_list_queued_page_is_still_accepted():
    """A link that only matched JOB_HREF (no gender marker of its own) is queued as a list page, not
    trusted outright -- but if the page it points at IS a real job detail page, its own JSON-LD says
    so, same signal job_links' fetch loop already uses. Proves the queued-candidate path isn't a
    strictly weaker path than job_links, just a deferred one."""
    seed_url = "https://example-klinik.de/karriere/"
    detail_url = "https://example-klinik.de/stellenangebote/42"
    seed_html = '<a href="/stellenangebote/42">Details</a>'   # anchor alone: no gender marker
    detail_html = JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Notaufnahme")
    fetch_map = {seed_url: R(seed_html, seed_url), detail_url: R(detail_html, detail_url)}
    cr = _crawler(fetch_map)

    rows, stats = cr._crawl_urls({"name": "X", "kez": "1", "career": seed_url}, {"example-klinik.de"}, [seed_url], [])

    assert len(rows) == 1 and rows[0]["title"] == "Pflegefachkraft (m/w/d) Notaufnahme"


def test_umantis_vacancy_with_no_gender_marker_is_recovered_via_its_own_unambiguous_url_shape():
    """Regression (2026-09-22 remediation round, reviewer finding #3). A real vacancy whose OWN
    anchor text on the page that links it carries no gender marker (JOB_TEXT) is queued as a list
    page, not job_links -- and umantis ships no JobPosting JSON-LD on its detail pages either, so
    before this fix it was silently lost, never entering the raw queue at all (confirmed live, clinic
    16211/recruitingapp-5545 umantis: 'Teamassistenz Ärztliche Direktion', Vacancies/720/
    Description/1, a real 12KB vacancy page -- 15 rows became 14). Its own URL shape
    (/Vacancies/<id>/Description/<n>) is unambiguous for this vendor -- umantis' own listing lives at
    the structurally different /Jobs/<n> (LINK_BAD) -- so it is trusted on its own, the same way
    JSON-LD already is for other boards."""
    seed_url = "https://recruitingapp-5545.de.umantis.com/Vacancies/720"
    detail_url = "https://recruitingapp-5545.de.umantis.com/Vacancies/720/Description/1"
    seed_html = '<a href="/Vacancies/720/Description/1">Teamassistenz Ärztliche Direktion</a>'  # no gender marker
    detail_html = ("<html><body><h1>Teamassistenz Ärztliche Direktion</h1>"
                   "<p>Wir suchen Verstaerkung. Jetzt bewerben!</p></body></html>")
    fetch_map = {seed_url: R(seed_html, seed_url), detail_url: R(detail_html, detail_url)}
    cr = _crawler(fetch_map)

    rows, stats = cr._crawl_urls({"name": "X", "kez": "1", "career": seed_url},
                                  {"recruitingapp-5545.de.umantis.com"}, [seed_url], [])

    assert detail_url in cr.calls              # queued as a list-page candidate, then fetched
    assert len(rows) == 1 and rows[0]["title"] == "Teamassistenz Ärztliche Direktion"


class _SitemapResp:
    def __init__(self, text):
        self.text, self.status_code = text, 200


def test_sitemap_job_urls_visits_every_child_of_an_unnamed_sitemap_index(monkeypatch):
    """TASK-72 AC#2: dropping just the old [:10] slice on Crawler.sitemap_job_urls left the
    per-child gate ("job-ish name in the child's own URL OR the index has <=4 children total")
    untouched -- an index with 5+ children none of them job-ish named (a plain WordPress-style
    sitemap-static-N.xml split, not the >10-children shape the slice alone covered) still matched
    that gate on NONE of them and silently visited zero children, the same class of bug as
    crawlers.vendor_adapters.find_job_urls' old locs[:3] this was meant to mirror."""
    base = "https://example-klinik.de"
    index_url = base + "/sitemap.xml"
    child_urls = [f"{base}/sitemap-static-{i}.xml" for i in range(1, 6)]   # 5, none job-ish named
    job_urls = [f"{base}/stellen/job-{i}" for i in range(1, 6)]
    pages = {index_url: "<sitemapindex>" + "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in child_urls) + "</sitemapindex>"}
    for cu, ju in zip(child_urls, job_urls):
        pages[cu] = f"<urlset><url><loc>{ju}</loc></url></urlset>"

    cr = _crawler({})
    monkeypatch.setattr(cr, "allowed", lambda url: True)
    monkeypatch.setattr(cr.s, "get", lambda url, timeout=40: _SitemapResp(pages.get(url, "")))

    found = cr.sitemap_job_urls(index_url)

    assert set(found) == set(job_urls)


# --- TASK-79: same-board host gate must not degenerate to a bare TLD ----------------------------

def test_page_hosts_ok_rejects_an_unrelated_domain_for_a_bare_apex_seed_host():
    """A seed host with no subdomain ('kbo-iak.de') used to lose its whole name to the one-label
    strip, leaving the bare TLD 'de' as the suffix every candidate was compared against -- so any
    .de host whose netloc merely contained 'job'/'karriere'/'softgarden'/'dvinci' passed as "the
    same board"."""
    cr = _crawler({})

    assert cr._page_hosts_ok("https://irrelevantsite-jobs.de/x", {"kbo-iak.de"}) is False
    assert cr._page_hosts_ok("https://karriere.fremde-klinik.de/stelle", {"kbo-iak.de"}) is False
    assert cr._page_hosts_ok("https://someone.softgarden.io/job/1", {"kbo-iak.de"}) is False
    # ...and the same for a two-label host that is not a .de domain at all.
    assert cr._page_hosts_ok("https://jobs.fremde.com/x", {"example.com"}) is False


def test_page_hosts_ok_still_allows_a_subdomain_hop_inside_the_same_registrable_domain():
    cr = _crawler({})

    assert cr._page_hosts_ok("https://karriere.example.de/stelle/1", {"www.example.de"}) is True
    assert cr._page_hosts_ok("https://jobs.example.de/1", {"karriere.example.de"}) is True
    assert cr._page_hosts_ok("https://tenant.softgarden.io/job/1", {"other.softgarden.io"}) is True
    assert cr._page_hosts_ok("https://tenant.dvinci-hr.com/de/jobs/10862/pflegehilfskraft", {"x.dvinci-hr.com"}) is True
    # the exact host is always in, keyword or not
    assert cr._page_hosts_ok("https://kbo-iak.de/kbo-karriere/x", {"kbo-iak.de"}) is True
    # ...and the apex of a registered www host is the same board too
    assert cr._page_hosts_ok("https://karriere.anregiomed.de/x", {"www.anregiomed.de"}) is True


def test_browser_crawler_link_gate_rejects_an_unrelated_domain_for_a_bare_apex_seed_host():
    """BrowserCrawler.crawl carried its own copy of the same widening, and without even the leading
    dot -- so 'notkbo-iak.de' passed too. Called unbound: __init__ would launch chromium."""
    from pflege_jobs.sources.career_browser import BrowserCrawler

    seed_url = "https://kbo-iak.de/karriere"
    links = [("https://irrelevantsite-jobs.de/stelle-1", "Pflegefachkraft (m/w/d)"),
             ("https://notkbo-iak.de/stelle-2", "Pflegefachkraft (m/w/d)"),
             ("https://karriere.kbo-iak.de/stelle-3", "Pflegefachkraft (m/w/d)")]

    fetched = []

    class _Stub:
        budget = 50
        def render(self, url, interact=True):
            return ("<html></html>" if interact else "", links, [], url)
        def fetch(self, url):
            fetched.append(url)
            return None

    rows, stats = BrowserCrawler.crawl(_Stub(), {"name": "kbo", "kez": "16251", "career": seed_url})

    assert rows == []
    assert stats["job_links_found"] == 1
    assert fetched == ["https://karriere.kbo-iak.de/stelle-3"]


def test_the_description_of_a_crawled_posting_reaches_the_role_classifier():
    # TASK-186: a training place listed under a role title ("Operationstechnische Assistenten (m/w/d)") says so
    # only in its body (real excerpt of a live posting).
    seed_url = "https://example-klinik.de/karriere/"
    job_url = "https://example-klinik.de/karriere/job-1"
    seed_html = '<a href="/karriere/job-1">Operationstechnische Assistenten (m/w/d)</a>'
    job_html = JOBPOSTING_TMPL.format(title="Operationstechnische Assistenten (m/w/d)").replace(
        "Wir suchen Verstaerkung.", "Ihre Voraussetzungen für die Ausbildung Sie haben einen Hauptschulabschluss (oder gleichwertig)")
    cr = _crawler({seed_url: R(seed_html, seed_url), job_url: R(job_html, job_url)})
    rows, _stats = cr.crawl({"name": "Example Klinik", "kez": "1", "career": seed_url, "town": "Muenchen"})
    assert [(r["role_class"], r["role_rule"].split(":")[0]) for r in rows] == [("ausbildung", "ausbildung_body")]


# --- TASK-185 / F8: LINK_BAD names a page TYPE, never the words of a job's own title slug ----------------------
# LINK_BAD substring-matched the whole URL, so a posting whose slug merely CONTAINS a bad word was dropped with no
# stat and no crawl issue. Frozen real URLs: UKA softgarden job 67659167 (sitemap, JobPosting JSON-LD, datePosted
# 2026-09-25) has "Datenschutz" in its slug; every "-ologin" profession noun contains "login"
# (Technologin: kliniken-gz-kru.de, klinikum-nuernberg.de, klinikum-straubing.de; Psychologin: diakonie-wuerzburg.de).
UKA_SLUG_URL = ("https://uk-augsburg.softgarden.io/job/67659167/Jurist-m-w-d-für-Datenschutz-und-IT-Recht-mit-"
                "vorgesehener-Übernahme-der-Funktion-als-Datenschutzbeauftragter-m-w-d-")
TECHNOLOGIN_URL = "https://kliniken-gz-kru.de/karriere/stellenangebote/detail/23-medizinische-r-technologe-technologin-fuer-radiologie-mtr-m-w-d"


def test_link_bad_does_not_read_the_words_of_a_job_slug():
    from pflege_jobs.sources.career_crawl import LINK_BAD
    assert not LINK_BAD.search(UKA_SLUG_URL)
    assert not LINK_BAD.search(TECHNOLOGIN_URL)
    assert not LINK_BAD.search("https://diakonie-wuerzburg.de/aktuelle-jobs/psychologin-(m-w-d)-beim-sozialpsychiatrischen-dienst.html")


def test_link_bad_still_names_the_page_types_it_always_did():
    from pflege_jobs.sources.career_crawl import LINK_BAD
    for u in ("https://www.klinikum-ab-alz.de/datenschutz",
              "https://www.klinikum-ab-alz.de/impressum",
              "https://karriere.ameos.eu/datenschutz/cookie-erklaerung",
              "https://uk-augsburg.softgarden.io/de/imprint",
              "https://jobs.klinikum-ab-alz.de/Login/3439",
              "https://recruitingapp-5545.de.umantis.com/Vacancies/Register/CheckLogin/1",
              "https://www.facebook.com/klinikum.ab.alz/",
              "https://twitter.com/KlinikumNbg",
              "https://www.addtoany.com/add_to/xing?linkurl=https%3A%2F%2Fkarriere.rottalinnkliniken.de%2Fjob%2Fx",
              "https://vk.com/share.php?url=https%3A%2F%2Fdongku.de%2Fstellenangebote%2Fx",
              "https://www.innklinikum.de/aerzteportal?tx_felogin_login%5Baction%5D=recovery&cHash=29768bf1",
              "https://www.jaegerwinkel.de/wGlobal/content/privacy/redirect-external.php?url=https://www.youtube.com/channel/x"):
        assert LINK_BAD.search(u), u


def test_a_sitemap_job_whose_slug_contains_a_bad_word_is_walked_not_dropped_silently():
    seed_url = "https://uk-augsburg.softgarden.io/"
    page = JOBPOSTING_TMPL.format(title="Jurist (m/w/d) für Datenschutz und IT-Recht")
    cr = _crawler({seed_url: R("<html></html>", seed_url), UKA_SLUG_URL: R(page, UKA_SLUG_URL)})
    cr.sitemap_job_urls = lambda sm: [UKA_SLUG_URL]
    rows, stats = cr._crawl_urls({"name": "UKA", "kez": "1", "career": seed_url, "town": "Augsburg"}, {"uk-augsburg.softgarden.io"},
                                 [seed_url], ["https://uk-augsburg.softgarden.io/sitemap.xml"])
    assert [r["source_url"] for r in rows] == [UKA_SLUG_URL]
    assert stats["job_pages"] == 1


def test_a_listed_job_whose_slug_contains_a_bad_word_passes_the_link_gate():
    cr = _crawler({})
    assert cr._page_hosts_ok(UKA_SLUG_URL, {"uk-augsburg.softgarden.io"}) is True
    assert cr._page_hosts_ok("https://uk-augsburg.softgarden.io/de/imprint", {"uk-augsburg.softgarden.io"}) is False


# --- TASK-185 / F4: a page the walk asked for and did not get is recorded, with its status and URL ------------------
# Crawler.fetch returned None for every non-200 and the walk reported truncated False: karriere.klinikverbund-allgaeu.de's
# job module answers with an HTTP 500 "TYPO3 Exception" (and, between 500s, a 200 shell with no jobs) -- the board read 84
# rows on run 225 and 19 on the next walk, and nothing said so.
class _HttpResp:
    def __init__(self, url, status, text="", ctype="text/html; charset=utf-8"):
        self.url, self.status_code, self.text, self.headers = url, status, text, {"content-type": ctype}


def _live_crawler(pages):
    """A Crawler with its REAL fetch over a fake session: pages = {url: (status, text)}; anything else answers 404."""
    cr = Crawler(towns={"muenchen"}, sleep=0, log=lambda *a, **k: None)
    cr.s.get = lambda url, timeout=40, allow_redirects=True: _HttpResp(url, *pages.get(url, (404,)))
    return cr


def test_fetch_records_a_non_200_with_its_status_and_url():
    cr = _live_crawler({"https://x.example/?id=49&type=9818": (500, "TYPO3 Exception")})
    assert cr.fetch("https://x.example/?id=49&type=9818") is None
    assert cr.failed == ["HTTP 500 https://x.example/?id=49&type=9818"]


def test_fetch_records_a_transport_failure_with_its_error_and_url():
    import requests
    cr = _live_crawler({})
    def boom(url, timeout=40, allow_redirects=True):
        raise requests.ConnectionError("reset")
    cr.s.get = boom
    assert cr.fetch("https://x.example/jobs") is None
    assert cr.failed == ["ConnectionError https://x.example/jobs"]


def test_fetch_does_not_record_a_robots_refusal_or_a_non_html_200():
    cr = _live_crawler({"https://x.example/a.pdf": (200, "%PDF", "application/pdf")})
    assert cr.fetch("https://x.example/a.pdf") is None
    cr.allowed = lambda url: False
    assert cr.fetch("https://x.example/private") is None
    assert cr.failed == []


def test_a_sitemap_that_does_not_answer_200_is_recorded_too():
    cr = _live_crawler({"https://x.example/sitemap.xml": (500, "TYPO3 Exception")})
    assert cr.sitemap_job_urls("https://x.example/sitemap.xml") == []
    assert cr.failed == ["HTTP 500 https://x.example/sitemap.xml"]


def test_crawl_reports_every_page_it_could_not_read_in_its_stats():
    seed_url = "https://x.example/karriere"
    listing = "https://x.example/?id=49&type=9818"
    job = "https://x.example/stelle/1"
    cr = _live_crawler({
        seed_url: (200, '<a href="%s">Stellenangebote</a><a href="%s">Pflegefachkraft (m/w/d) Station 1</a>' % (listing, job)),
        listing: (500, "TYPO3 Exception"),
        job: (200, JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Station 1"))})
    rows, stats = cr.crawl({"name": "X", "kez": "1", "career": seed_url, "town": "Muenchen"})
    assert [r["source_url"] for r in rows] == [job]              # what could be read is still returned
    assert stats["failed_pages"] == ["HTTP 500 " + listing]


def test_crawl_with_every_page_answering_reports_no_failed_pages():
    seed_url = "https://x.example/karriere"
    job = "https://x.example/stelle/1"
    cr = _live_crawler({seed_url: (200, '<a href="%s">Pflegefachkraft (m/w/d) Station 1</a>' % job),
                        job: (200, JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Station 1"))})
    assert cr.crawl({"name": "X", "kez": "1", "career": seed_url, "town": "Muenchen"})[1]["failed_pages"] == []


# --- TASK-185 / F4 (second pass): which of the pages that did not answer make the read INCOMPLETE --------------------
# stats["failed_pages"] lists every page the walk asked for and did not get (information). stats["incomplete_pages"] -- what
# app/crawl.py records as the crawl_issue kind 'incomplete', the one that blocks absence-retirement -- lists only the pages
# the walk itself needed, told apart by the walk's own variables: the seed and every start_url (seed, extra_seeds, the
# section-first subtree), a page it queued under PAGINATE, a sitemap / sitemap index, a URL in job_links (an anchor with a
# posting-shaped text, or a sitemap job URL) -- and any 5xx or transport error on any page. A 4xx on a link the walk merely
# followed (queued as a list page by JOB_HREF / LIST_NAV) is information only.
SEED = "https://x.example/karriere"
JOB_URL = "https://x.example/stelle/1"
JOB_PAGE = (200, JOBPOSTING_TMPL.format(title="Pflegefachkraft (m/w/d) Station 1"))
JOB_ANCHOR = '<a href="%s">Pflegefachkraft (m/w/d) Station 1</a>' % JOB_URL


def _walk(pages, seed_url=SEED, **seed):
    return _live_crawler(pages).crawl({"name": "X", "kez": "1", "career": seed_url, "town": "Muenchen", **seed})[1]


@pytest.mark.parametrize("seed_url,dead,status", [
    # anregiomed.de/karriere-jobs: dead "Weiterbildung" navigation (LIST_NAV) and a "/detail/" news index (JOB_HREF), both queued as list pages
    ("https://www.anregiomed.de/karriere-jobs/", "https://www.anregiomed.de/medizin-und-pflege/klinikum-ansbach/urologie/weiterbildung", 404),
    ("https://www.anregiomed.de/karriere-jobs/", "https://www.anregiomed.de/aktuelles/neuigkeiten/detail/", 404),
    # uk-augsburg.softgarden.io: Wicket callback links ("/vacancies?-1.-jobSearch-...") answer 403 to a plain GET
    ("https://uk-augsburg.softgarden.io/de/vacancies", "https://uk-augsburg.softgarden.io/de/vacancies?-1.-jobSearch-jobSearchContainer-internalLink", 403),
    # karriere.klinikverbund-allgaeu.de: an image srcset string ("<png>, /fileadmin/... 480w") read as a link; "Verbundweiterbildung" matches LIST_NAV
    ("https://karriere.klinikverbund-allgaeu.de/karriere",
     "https://karriere.klinikverbund-allgaeu.de/fileadmin/_processed_/b/4/csm_Broschuere_Verbundweiterbildung_Unterallgaeu_2023_a2da198e0a.png, "
     "/fileadmin/_processed_/b/4/csm_Broschuere_Verbundweiterbildung_Unterallgaeu_2023_16532e5f92.png 480w", 404),
    (SEED, "https://x.example/karriere/weiterbildung", 410),
])
def test_a_dead_followed_link_is_failed_information_but_does_not_make_the_read_incomplete(seed_url, dead, status):
    stats = _walk({seed_url: (200, '<a href="%s">Mehr</a>' % dead), dead: (status, "Not found")}, seed_url)
    assert stats["failed_pages"] == ["HTTP %d %s" % (status, dead)]
    assert stats["incomplete_pages"] == []


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_error_on_a_followed_link_makes_the_read_incomplete(status):
    stray = "https://x.example/karriere/weiterbildung"
    stats = _walk({SEED: (200, '<a href="%s">Mehr</a>' % stray), stray: (status, "TYPO3 Exception")})
    assert stats["failed_pages"] == stats["incomplete_pages"] == ["HTTP %d %s" % (status, stray)]


def test_a_transport_failure_on_a_followed_link_makes_the_read_incomplete():
    import requests
    stray = "https://x.example/karriere/weiterbildung"
    cr = _live_crawler({SEED: (200, '<a href="%s">Mehr</a>' % stray)})
    answer = cr.s.get
    def get(url, **kw):
        if url == stray:
            raise requests.ConnectionError("reset")
        return answer(url, **kw)
    cr.s.get = get
    stats = cr.crawl({"name": "X", "kez": "1", "career": SEED, "town": "Muenchen"})[1]
    assert stats["failed_pages"] == stats["incomplete_pages"] == ["ConnectionError " + stray]


SITEMAP = "https://x.example/sitemap.xml"
SITEMAP_CHILD = "https://x.example/sitemap-jobs.xml"
PAGE_2 = "https://x.example/stellen?page=2"
LISTING = "https://x.example/stellen"
SECTION = "https://x.example/karriere/pflege/"
URLSET = "<urlset><url><loc>%s</loc></url></urlset>" % JOB_URL
SITEMAP_INDEX = "<sitemapindex><sitemap><loc>%s</loc></sitemap></sitemapindex>" % SITEMAP_CHILD


@pytest.mark.parametrize("pages,seed,dead,status", [
    pytest.param({}, {}, SEED, 404, id="seed"),
    pytest.param({SEED: (200, "<p>Jobs</p>")}, {"extra_seeds": [LISTING]}, LISTING, 404, id="extra_seed"),
    pytest.param({SEED: (200, "<p>Jobs</p>")}, {"extra_seeds": [LISTING]}, LISTING, 403, id="extra_seed_403"),
    pytest.param({SEED: (200, '<a href="%s">2</a>' % PAGE_2)}, {}, PAGE_2, 404, id="pagination"),
    pytest.param({SEED: (200, '<a href="%s">2</a>' % PAGE_2)}, {}, PAGE_2, 403, id="pagination_403"),
    pytest.param({SEED: (200, '<a href="/karriere/pflege/">Pflegedienst</a>')}, {}, SECTION, 404, id="section_subtree"),
    pytest.param({SEED: (200, "<p>Jobs</p>")}, {"sitemaps": [SITEMAP]}, SITEMAP, 404, id="sitemap"),
    pytest.param({SEED: (200, "<p>Jobs</p>"), SITEMAP: (200, SITEMAP_INDEX)}, {"sitemaps": [SITEMAP]}, SITEMAP_CHILD, 404, id="sitemap_index_child"),
    pytest.param({SEED: (200, JOB_ANCHOR)}, {}, JOB_URL, 404, id="job_detail"),
    pytest.param({SEED: (200, JOB_ANCHOR)}, {}, JOB_URL, 403, id="job_detail_403"),
    pytest.param({SEED: (200, "<p>Jobs</p>"), SITEMAP: (200, URLSET)}, {"sitemaps": [SITEMAP]}, JOB_URL, 404, id="sitemap_job_detail"),
])
def test_a_page_the_walk_needed_that_does_not_answer_makes_the_read_incomplete(pages, seed, dead, status):
    stats = _walk({**pages, dead: (status, "Not found")}, SEED, **seed)
    entry = "HTTP %d %s" % (status, dead)
    assert entry in stats["failed_pages"]
    assert stats["incomplete_pages"] == [entry]        # once: the seed (and a section page) is asked for twice


def test_failed_pages_lists_every_page_that_did_not_answer_incomplete_pages_only_the_needed_ones():
    stray = "https://x.example/karriere/weiterbildung"
    stats = _walk({SEED: (200, '<a href="%s">Mehr</a><a href="%s">2</a>' % (stray, PAGE_2))})
    assert stats["failed_pages"] == ["HTTP 404 " + stray, "HTTP 404 " + PAGE_2]
    assert stats["incomplete_pages"] == ["HTTP 404 " + PAGE_2]


def test_crawl_with_every_page_answering_reports_no_incomplete_pages():
    stats = _walk({SEED: (200, JOB_ANCHOR), JOB_URL: JOB_PAGE})
    assert stats["failed_pages"] == [] and stats["incomplete_pages"] == []
