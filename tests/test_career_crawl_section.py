"""career_crawl.Crawler: section-first BFS gate. Pure in-process fake fetch, no network.

Contract under test (see pflege_jobs/section.py + the task note in ats_seeds/career_crawl):
  - a confident nursing-section link on the seed page -> walk ONLY that subtree first (depth<=2);
  - if that subtree yields zero jobs -> fall back to the original full board-wide walk;
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


def test_crawl_urls_flags_truncated_when_the_queue_size_ceiling_drops_a_pagination_candidate():
    """TASK-72 AC#2: a pagination candidate dropped by the len(seen_lists)+len(list_q) >=
    list_budget*2 queue-size ceiling used to leave no trace at all when the (small) surviving queue
    still drained naturally afterwards -- bool(list_q) alone never caught it."""
    seed_url = "https://example-klinik.de/karriere/"
    next_url = "https://example-klinik.de/karriere/?page=2"
    # 6 identical "next page" links on the one seed page: with list_budget=3 (cap = list_budget*2 =
    # 6), the first 5 occurrences queue fine and drain for free once the real url is deduped; the
    # 6th is silently dropped by the size cap alone, with the page-count ceiling never engaged.
    seed_html = "".join('<a href="/karriere/?page=2">weiter</a>' for _ in range(6))
    dead_end_html = "<html><body>keine weiteren Seiten</body></html>"
    fetch_map = {seed_url: R(seed_html, seed_url), next_url: R(dead_end_html, next_url)}
    cr = _crawler(fetch_map, list_pages=3)

    rows, stats = cr._crawl_urls({"name": "X", "kez": "1", "career": seed_url}, {"example-klinik.de"}, [seed_url], [])

    assert stats["list_pages"] < cr.list_budget   # drained naturally, well under the page-count ceiling
    assert stats["truncated"] is True             # ...yet a real candidate was dropped by the size cap


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
