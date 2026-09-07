"""softgarden adapter: host detection + /jobs.feed.json. Pure functions -> stub session, no network."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources.softgarden import SG_HOST, fetch_feed, seed_for   # noqa: E402


class FakeResp:
    def __init__(self, text="", status_code=200, url="", json_data=None):
        self.text, self.status_code, self.url = text, status_code, url
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


def test_sg_host_matches_tenant_career_subdomain():
    # this shape (<tenant>.career.softgarden.de) is what softgarden boards actually use today
    assert SG_HOST.search("https://danuviusklinik.career.softgarden.de/").group(1) == "danuviusklinik.career.softgarden.de"


def test_sg_host_still_matches_legacy_shapes():
    assert SG_HOST.search("https://foo.softgarden.io/vacancies").group(1) == "foo.softgarden.io"
    assert SG_HOST.search("https://jobdb.softgarden.de/some-slug/x").group(1) == "jobdb.softgarden.de/some-slug"


def test_seed_for_tries_own_domain_before_detected_host():
    # custom domains (jobs.pkd.de, karriere.klinikum-bayreuth.de) often proxy /jobs.feed.json straight onto
    # their own domain even though the page links point at a *.softgarden.io host -- try our own domain first.
    html = '<a href="https://tenant.softgarden.io/get-connected">x</a>'
    s = FakeSession({"https://karriere.example.de/": FakeResp(text=html, url="https://karriere.example.de/")})
    seed = seed_for({"name": "X", "career": "https://karriere.example.de/"}, "K1", "Town", session=s)
    assert seed["feed_hosts"] == ["https://karriere.example.de", "https://tenant.softgarden.io"]


def test_seed_for_returns_none_when_no_softgarden_host_found():
    s = FakeSession({"https://plain.example.de/": FakeResp(text="<html>nothing here</html>", url="https://plain.example.de/")})
    assert seed_for({"name": "X", "career": "https://plain.example.de/"}, "K1", "Town", session=s) is None


def test_fetch_feed_skips_404_and_returns_first_hit():
    good = {"dataFeedElement": [{"item": {"@type": "JobPosting", "title": "x"}}]}
    s = FakeSession({
        "https://a.example/jobs.feed.json": FakeResp(status_code=404, url="https://a.example/jobs.feed.json"),
        "https://b.example/jobs.feed.json": FakeResp(status_code=200, url="https://b.example/jobs.feed.json", json_data=good),
    })
    items, host = fetch_feed(["https://a.example", "https://b.example"], session=s)
    assert host == "https://b.example"
    assert items == [{"@type": "JobPosting", "title": "x"}]


def test_fetch_feed_returns_none_when_all_hosts_miss():
    items, host = fetch_feed(["https://a.example", "https://b.example"], session=FakeSession({}))
    assert items is None and host is None
