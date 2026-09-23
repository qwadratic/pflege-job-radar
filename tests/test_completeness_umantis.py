"""Adapter-specific red tests for umantis (TASK-38) -- offline, no network. The shared harness
(tests/test_adapter_completeness.py + tests/adapter_contract.py) covers the five checks generically;
this module pins down umantis-only behaviour the shared checks cannot see: what the *board itself*
exposes per template, and the read-path/pagination shapes specific to Haufe umantis.

Fixtures below are trimmed copies of real live umantis detail pages (recruitingapp-5545 and
recruitingapp-5580, fetched 2026-09-10, full copies under crawl_snapshots/); using invented markup
here would test our own assumptions, not the board.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers.routing import ADAPTERS
from pflege_jobs.sources import ats_seeds
from pflege_jobs.sources.career_crawl import LINK_BAD, JOB_HREF, Crawler
from tests import adapter_contract as AC


SEED = {"name": "Test Klinik", "kez": "1", "career": "https://recruitingapp-5545.de.umantis.com/Jobs/1", "town": None}

# recruitingapp-5545 (kbo-Kinderzentrum München), Vacancies/438/Description/1: exposes a
# "Veröffentlichung ab" publish date and an "Arbeitszeit" line -- the one umantis template among the
# 5 live boards that states a publish date anywhere on the page (see TASK-38 report: the other 4
# templates -- 5556, 5580, 5610, 5511 -- only ever state a job *start* date, never a publish date).
DETAIL_WITH_DATE = """
<html><head><title>Pflegefachkraft (w/m/d)</title></head><body>
<div class="content-details"><div class="Info-Text">Ein Angebot fuer Sie.</div>
<p><strong>Wir suchen</strong></p><div class="title">Pflegefachkraft (w/m/d)</div>
<p><strong>Arbeitszeit</strong></p><p>Vollzeit (38,5 Stunden/Woche)</p>
<p><strong>Veröffentlichung ab</strong></p><p><p>28.07.2026</p></p>
</div><a href="/Vacancies/438/Application/CheckLogin/1">Jetzt bewerben</a></body></html>
"""

# recruitingapp-5580 (St. Vinzenz Klinik), Vacancies/588/Description/1: a fully custom per-tenant
# template with no publish-date label at all -- only "zum <date>" (job start date, a different field).
DETAIL_WITHOUT_DATE = """
<html><head><title>Auszubildende/n (m/w/d)</title></head><body>
<div class="conts">Zur Verstärkung unseres Teams suchen wir <b>zum 01.09.2027</b> eine/n</div>
<div class="conts"><h1>Auszubildende/n zur/zum Kauffrau/-mann (m/w/d)</h1></div>
<div class="conts contsh"><b>in Vollzeit (38,5 Std./Woche) in Pfronten</b></div>
<a href="/Vacancies/588/Application/CheckLogin/1">Online-Bewerbung</a></body></html>
"""


def test_date_and_employment_type_extracted_when_the_template_states_them():
    cr = Crawler(towns={"münchen"})
    row = cr._heuristic(DETAIL_WITH_DATE, "https://recruitingapp-5545.de.umantis.com/Vacancies/438/Description/1", SEED)
    assert row["first_published"] == "2026-07-28"
    assert row["employment_types"] == ["vollzeit"]


def test_no_date_fabricated_when_the_template_never_states_one():
    # The board is the oracle: a per-tenant template that never states a publish date (only a job
    # start date, "zum 01.09.2027") must leave first_published None, not repurpose the start date --
    # that would misrepresent the source, not report it. See TASK-38 report for the 4 affected boards.
    cr = Crawler(towns={"pfronten"})
    row = cr._heuristic(DETAIL_WITHOUT_DATE, "https://recruitingapp-5580.de.umantis.com/Vacancies/588/Description/1", SEED)
    assert row["first_published"] is None
    assert row["employment_types"] == ["vollzeit"]


def test_pagination_listing_page_is_never_treated_as_a_posting():
    # /Jobs/<n> is umantis's own paginated listing, in every UI language -- its language-switcher
    # anchors on the listing page match JOB_HREF's /vacanc pattern too (it self-links there), so
    # without the LINK_BAD exclusion the crawler would "discover" its own list pages as job details.
    url = "https://recruitingapp-5545.de.umantis.com/Jobs/3?lang=ger"
    assert JOB_HREF.search(url)          # still looks job-ish by path shape
    assert LINK_BAD.search(url)          # but is excluded before ever being fetched as a detail page


def test_bare_jobs_1_seed_is_offered_for_read_path_coverage(monkeypatch):
    # tests/adapter_contract.py's client_read_paths finds the board's own embedded reference to
    # /Jobs/1 with no query string (St. Vinzenz's karriere-vinzenz-klinik.de hub page embeds the
    # umantis board URL with a DesignID query, live); the completeness harness matches read paths by
    # endpoint shape (host + path + query param NAMES), so a call that always carries extra params
    # never satisfies that bare shape. Confirmed live: St. Vinzenz.
    class _Resp:
        def __init__(self, text):
            self.text = text

    hub_html = '<a href="https://recruitingapp-5580.de.umantis.com/Jobs/1?lang=ger&amp;DesignID=10008&amp;message=">Stellenangebote</a>'
    monkeypatch.setattr(ats_seeds, "_get", lambda u: _Resp(hub_html))
    f = {"name": "St. Vinzenz Klinik", "career": "https://karriere-vinzenz-klinik.de/stellenportal/"}
    seed = ats_seeds.umantis(f, "77705", "Pfronten")
    bare = "https://recruitingapp-5580.de.umantis.com/Jobs/1"
    assert bare in seed["extra_seeds"]
    assert AC.endpoint_key(bare) == AC.endpoint_key("https://recruitingapp-5580.de.umantis.com/Jobs/1")


def test_umantis_routes_through_the_seeded_ats_seeds_builder():
    # Pin the routing table entry TASK-38 owns: umantis is "seeded" through ats_seeds.umantis, not
    # the unused crawlers.portals:parse_umantis dead code (see crawlers/routing.py's own comment).
    kind, ref = ADAPTERS["umantis"]
    assert kind == "seeded"
    assert ref == "pflege_jobs.sources.ats_seeds:umantis"


def test_app_crawl_umantis_branch_adds_no_per_board_ceiling_of_its_own(monkeypatch):
    """TASK-14: app/crawl.py's umantis branch ran Crawler(per_site_pages=150), a detail-fetch
    ceiling on a free board that the Crawler-level no-cap tests could not see because they build
    their own Crawler. The production caller is the thing under test here: a 200-job board must
    come back whole through _seed_obs, not sliced to the caller's own number."""
    import app.crawl as CR

    n = 200
    hrefs = [f"/Vacancies/{i}/Description/1" for i in range(n)]
    list_html = "".join(f'<a href="{h}">Pflegefachkraft (m/w/d) Station {i}</a>' for i, h in enumerate(hrefs))
    detail = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Pflegefachkraft (m/w/d)", '
              '"description": "d", "datePosted": "2026-01-01", '
              '"jobLocation": {"address": {"addressLocality": "M\\u00fcnchen"}}}</script>')
    host = "https://recruitingapp-5511.de.umantis.com"
    pages = {f"{host}/Jobs/1": list_html}
    pages.update({host + h: detail for h in hrefs})

    class _Resp:
        def __init__(self, url, text):
            self.url, self.text, self.status_code = url, text, 200
            self.headers = {"content-type": "text/html"}

    monkeypatch.setattr(Crawler, "fetch", lambda self, url: (_Resp(url, pages[url]) if url in pages else None))
    monkeypatch.setitem(ats_seeds.BUILDERS, "umantis", lambda f, kez, town: {
        "name": "Test Klinik", "kez": kez, "career": f"{host}/Jobs/1",
        "hosts": ["recruitingapp-5511.de.umantis.com"], "extra_seeds": [], "sitemaps": []})

    clinic = {"clinic_id": "56101", "name": "Test Klinik", "careers_url": f"{host}/Jobs/1", "town": "Ansbach"}
    rows, stats = CR._seed_obs({"vendor": "umantis"}, clinic, {"münchen"}, log=lambda *a, **k: None)

    assert stats["job_links_found"] == n
    assert len(rows) == n
    assert stats["truncated"] is False
