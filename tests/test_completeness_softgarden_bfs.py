"""Adapter-specific completeness regressions for softgarden-bfs (TASK-31) -- the shared harness in
tests/test_adapter_completeness.py only asserts declared-total parity when the board's own page has a
parseable "N Stellen" count string; several live softgarden BFS-fallback boards (no jobs.feed.json,
e.g. Leopoldina) never print one, so a silent per-board cap there would never turn the generic checks
red. These tests exercise career_crawl.Crawler's own stop condition directly, offline:

  no cap       the detail-fetch loop must walk every job link the list/sitemap walk found, not slice
               to a fixed per_site_pages count (confirmed live 2026-09-10: heiligenfeld undercounted
               145/169, uk-augsburg 147/225 under the old per_site_pages=150 override in app/crawl.py).
  truncated    when a real safety ceiling IS hit, that must be recorded as stats["truncated"]=True --
               never indistinguishable from a board that was walked to its own end.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources.career_crawl import Crawler  # noqa: E402


class _FakeResp:
    def __init__(self, url, text):
        self.url, self.text, self.status_code = url, text, 200
        self.headers = {"content-type": "text/html"}


def _seed_and_pages(n_jobs):
    """One list page linking n_jobs job detail pages (no pagination), each a JSON-LD JobPosting."""
    hrefs = [f"/job/{i}/Pflegefachkraft-Station-{i}-m-w-d" for i in range(n_jobs)]
    list_html = "<html><body>" + "".join(
        f'<a href="{h}">Pflegefachkraft (m/w/d) Station {i}</a>' for i, h in enumerate(hrefs)
    ) + "</body></html>"
    pages = {"https://example.test/de/vacancies": list_html}
    for i, h in enumerate(hrefs):
        u = "https://example.test" + h
        jsonld = (
            '<script type="application/ld+json">{"@type": "JobPosting", "title": "Pflegefachkraft %d", '
            '"description": "desc", "datePosted": "2026-01-01", "employmentType": "FULL_TIME", '
            '"jobLocation": {"address": {"addressLocality": "München"}}}</script>' % i
        )
        pages[u] = f"<html><body>{jsonld}</body></html>"
    seed = {"name": "Test Clinic", "kez": "K1", "career": "https://example.test/de/vacancies",
            "hosts": ["example.test"], "extra_seeds": [], "sitemaps": [], "town": None}
    return seed, pages


def _wire_fake_fetch(cr, pages):
    def fake_fetch(url):
        html = pages.get(url)
        return _FakeResp(url, html) if html is not None else None
    cr.fetch = fake_fetch


def test_detail_fetch_walks_every_job_link_found_no_fixed_slice():
    n = 200   # more than the old per_site_pages=150 override this regression guards against
    seed, pages = _seed_and_pages(n)
    cr = Crawler(towns=set(), sleep=0)
    _wire_fake_fetch(cr, pages)
    rows, stats = cr.crawl(seed)
    assert stats["job_links_found"] == n
    assert len(rows) == n
    assert stats["truncated"] is False


def test_safety_ceiling_hit_is_recorded_truncated_not_silent_success():
    n = 200
    seed, pages = _seed_and_pages(n)
    cr = Crawler(towns=set(), per_site_pages=50, sleep=0)   # ceiling deliberately below what's found
    _wire_fake_fetch(cr, pages)
    rows, stats = cr.crawl(seed)
    assert stats["job_links_found"] == n
    assert len(rows) == 50
    assert stats["truncated"] is True


def test_detail_fetch_failure_is_distinguishable_from_a_genuinely_empty_board():
    """BFS-fallback boards (no jobs.feed.json, no printed 'N Stellen' total -- the shared harness's
    declared_total_parity/field_completeness both special-case 0 rows as 'nothing to compare', so a
    detail-fetch regression on a board like this is invisible to the shared completeness suite; see
    live verification 2026-09-10 on gebo-med: blocking every JOB_HREF-shaped fetch dropped 63 -> 0
    rows while job_links_found stayed 67). stats must keep telling the two cases apart: job_links_found
    > 0 with job_pages == 0 (links seen, every detail fetch failed) is not the same as a board with no
    job links at all -- so a future consumer of these stats (unlike the shared harness today) has
    something honest to act on."""
    n = 50
    seed, pages = _seed_and_pages(n)
    cr = Crawler(towns=set(), sleep=0)

    def fetch_list_only(url):
        html = pages.get(url)
        return _FakeResp(url, html) if html is not None and url == seed["career"] else None

    cr.fetch = fetch_list_only
    rows, stats = cr.crawl(seed)
    assert stats["job_links_found"] == n     # the list page's own links were found
    assert stats["job_pages"] == 0           # but every single detail fetch failed
    assert len(rows) == 0                    # -- a real failure, not "board has zero jobs"
