"""Adapter-specific completeness regressions for bite (TASK-37) -- the shared harness in
tests/test_adapter_completeness.py covers the five generic checks against live boards; this module
locks in fixes the live run found that the generic checks can't exercise offline:

  pagination   walk_all_postings must walk page.offset to page.total (the board's own end signal),
               not assume one call of a fixed size covers every board forever.
  no caps      _fallback_jobposting_links must not silently drop postings past an arbitrary limit.
  fallback     crawl() must fall through to the same-origin fallbacks when the only widget mount on
               the page is non-functional (e.g. Artemed's 'niiid' chatbot bundle), not give up.
  field        the _json.jobs.php fallback (klinikum-gap.de-shaped) must derive employmentType from
               whichever custom_field* slot carries "Vollzeit"/"Teilzeit" text -- verified missing
               live 2026-09-10 (0/56 rows on karriere.klinikum-gap.de before the fix).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources import bite  # noqa: E402


# --- employmentType from the _json.jobs.php fallback's free-text custom fields -----------------

def test_json_jobs_php_derives_employment_type_from_custom_field_text():
    # gap-shaped: custom_field4 happens to carry "Vollzeit / Teilzeit" -- field NAME is tenant-
    # specific (surveyed live), so this must scan every custom_field*, not assume field4.
    ad = {"title": "Apotheker (m/w/d)", "url": {"href": "https://jobs.klinikum-gap.de/jobposting/abc"},
          "address": {"city": "82467 Garmisch-Partenkirchen"},
          "custom_field1": "MedizinTechnDienst", "custom_field4": "Vollzeit / Teilzeit"}
    jp = bite._jp_from_json_jobs_php(ad)
    assert set(jp["employmentType"]) == {"full_time", "part_time"}


def test_json_jobs_php_employment_type_empty_when_no_field_mentions_it():
    ad = {"title": "Koch (m/w/d)", "url": {"href": "https://x/jobposting/def"},
          "address": {"city": "München"}, "custom_field1": "Küche"}
    jp = bite._jp_from_json_jobs_php(ad)
    assert jp["employmentType"] == []


# --- TASK-73 AC11: job_site (per-ad) must win over address (tenant HQ, identical on every ad) ---

def test_json_jobs_php_prefers_job_site_over_tenant_hq_address():
    # klinikum-gap.de-shaped: every ad carries the Garmisch-Partenkirchen HQ address, but this one
    # is actually a Murnau posting -- job_site names that.
    ad = {"title": "Pflegefachkraft (m/w/d)", "url": {"href": "https://jobs.klinikum-gap.de/jobposting/xyz"},
          "address": {"city": "82467 Garmisch-Partenkirchen"}, "job_site": "Murnau"}
    jp = bite._jp_from_json_jobs_php(ad)
    assert jp["address"]["city"] == "Murnau"


def test_json_jobs_php_falls_back_to_tenant_address_when_no_job_site():
    ad = {"title": "Pflegefachkraft (m/w/d)", "url": {"href": "https://jobs.klinikum-gap.de/jobposting/abc"},
          "address": {"city": "82467 Garmisch-Partenkirchen"}}
    jp = bite._jp_from_json_jobs_php(ad)
    assert jp["address"]["city"] == "Garmisch-Partenkirchen" and jp["address"]["postCode"] == "82467"


def test_json_jobs_php_splits_a_plz_prefixed_job_site_too():
    ad = {"title": "Pflegefachkraft (m/w/d)", "url": {"href": "https://jobs.klinikum-gap.de/jobposting/qrs"},
          "address": {"city": "82467 Garmisch-Partenkirchen"}, "job_site": "82418 Murnau"}
    jp = bite._jp_from_json_jobs_php(ad)
    assert jp["address"]["city"] == "Murnau" and jp["address"]["postCode"] == "82418"


# --- pagination: walk_all_postings walks the board's own total, not a fixed page ----------------

class _PagedFakeSession:
    """Two postings per page.num=2 call; page.total tells the walker when to stop."""
    def __init__(self, all_jps):
        self.all = all_jps
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        off = json["page"].get("offset", 0)
        size = json["page"]["num"]
        self.calls.append((off, size))
        page = self.all[off:off + size]

        class R:
            def raise_for_status(_self): pass
            def json(_self): return {"jobPostings": page, "page": {"offset": off, "total": len(self.all)}}
        return R()


def test_walk_all_postings_paginates_to_the_boards_own_total():
    all_jps = [{"title": f"Job {i}", "url": f"https://x/{i}"} for i in range(7)]
    fake = _PagedFakeSession(all_jps)
    got, err, board_total = bite.walk_all_postings("key", fake, page_size=2)
    assert err is None
    assert [j["title"] for j in got] == [j["title"] for j in all_jps]
    assert len(fake.calls) == 4   # 7 postings / page_size 2 -> 4 calls (2,2,2,1), not one guessed call
    assert board_total == 7


def test_walk_all_postings_single_call_when_first_page_already_covers_total():
    all_jps = [{"title": "only one", "url": "https://x/0"}]
    fake = _PagedFakeSession(all_jps)
    got, err, board_total = bite.walk_all_postings("key", fake, page_size=1000)
    assert err is None
    assert len(got) == 1
    assert len(fake.calls) == 1
    assert board_total == 1


class _FailingPostSession:
    def post(self, url, headers=None, json=None, timeout=None):
        raise __import__("requests").exceptions.ConnectionError("mutation: blocked")


def test_walk_all_postings_reports_the_failure_instead_of_raising():
    # 2026-09 regression: search() used to call raise_for_status() unguarded -- a blocked/broken
    # search endpoint crashed the whole crawl() instead of a clean (rows=[], error=...) like every
    # other fetch in this module already returns on failure (api_key, posting_html, the fallbacks).
    got, err, board_total = bite.walk_all_postings("key", _FailingPostSession())
    assert got == []
    assert err and "search failed" in err
    assert board_total is None


# --- _fallback_jobposting_links: no invented cap -------------------------------------------------

class _LinkFakeSession:
    def __init__(self):
        self.fetched = []

    def get(self, url, headers=None, timeout=None):
        self.fetched.append(url)
        ld = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "T %s"}</script>'
              % url.rsplit("/", 1)[-1])

        class R:
            status_code = 200
            text = f"<html>{ld}</html>"
            def raise_for_status(_self): pass
        return R()


def test_fallback_jobposting_links_has_no_cap(monkeypatch):
    monkeypatch.setattr(bite.time, "sleep", lambda *_: None)   # politeness sleep is real HTTP concern, not this test's
    hexes = [f"{i:040x}" for i in range(200)]   # more than the old limit=150
    html = "".join(f'<a href="https://example.de/jobposting/{h}">x</a>' for h in hexes)
    fake = _LinkFakeSession()
    jps = bite._fallback_jobposting_links(html, fake)
    assert len(jps) == 200


# --- crawl(): a non-functional widget mount (chatbot) must fall through to the fallbacks --------

class _NoKeySession:
    """Simulates a page whose only mount is a non-listing bundle (empty key) -- api_key() returns
    None -- with no _json.jobs.php and no jobposting links either, so crawl() must report the
    no-listing error rather than silently returning [] with no explanation."""
    def get(self, url, headers=None, timeout=None):
        class R:
            status_code = 404
            text = ""
            def raise_for_status(_self):
                import requests
                raise requests.RequestException("404")
            def json(_self):
                raise ValueError("no json")
        return R()


def test_crawl_reports_the_real_reason_when_only_mount_is_non_functional_and_no_fallback_exists(monkeypatch):
    html = '<script src="//static.b-ite.com/jobs-api/loader-v1/x.js"></script><div data-bite-jobs-api-listing="artemed-8:niiid"></div>'

    class PageSession(_NoKeySession):
        def get(self, url, headers=None, timeout=None):
            if url == "https://example.de/karriere":
                class R:
                    status_code = 200
                    text = html
                return R()
            return super().get(url, headers=headers, timeout=timeout)

    monkeypatch.setattr(bite.requests, "Session", lambda: PageSession())
    monkeypatch.setattr(bite, "api_key", lambda customer, listing, session=None: None)
    seed = {"name": "Feldafing-shaped", "kez": "K9", "career": "https://example.de/karriere"}
    rows, stats = bite.crawl(seed, {"münchen"}, with_descriptions=False)
    assert rows == []
    assert "artemed-8" in stats["error"] and "niiid" in stats["error"]   # names the mount it tried, not a silent []


def test_crawl_carries_walk_all_postings_own_board_total_into_its_stats(monkeypatch):
    """TASK-88 AC#1: crawl() must not just USE walk_all_postings' board_total to drive the walk
    (that happens inside walk_all_postings itself) -- it must also forward it into stats, or
    app/crawl.py's consumer never sees it."""
    monkeypatch.setattr(bite, "api_key", lambda customer, listing, session=None: "deadbeef")
    monkeypatch.setattr(bite, "walk_all_postings", lambda key, session=None, page_size=1000: (
        [{"title": "Pflegefachkraft (m/w/d)", "url": "https://x/1"}], None, 57))
    seed = {"name": "Klinik X", "kez": "K1", "career": "https://x.example/karriere",
            "customer": "cust1", "listing": "list1"}
    rows, stats = bite.crawl(seed, {"x"}, with_descriptions=False)
    assert len(rows) == 1
    assert stats["board_total"] == 57
    assert stats["total"] == 1   # rows actually fetched, distinct from the board's own declared total
