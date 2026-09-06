"""Parsers for the walled aggregator (Indeed) and the JS portals. Pure functions -> no network."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crawlers"))

from portals import (JS_PORTALS, indeed_urls, parse_jobposting_feed, parse_umantis)   # noqa: E402


UMANTIS_HTML = """
<table>
 <tr><td><a href="/Vacancies/2464/Description/1">Oberarzt Gastroenterologie (m/w/d)</a>
         <a href="/Vacancies/2464/Application/CheckLogin/1">Bewerben</a></td></tr>
 <tr><td><a href="/Vacancies/577/Description/1"><span>Pflegefachkraft (m/w/d)</span> f&uuml;r die ZNA</a></td></tr>
 <tr><td><a href="/Vacancies/2464/Description/1">Oberarzt Gastroenterologie (m/w/d)</a></td></tr>
 <tr><td><a href="/Vacancies/999/Description/1">   </a></td></tr>
</table>
"""


def test_parse_umantis_dedupes_by_vacancy_id():
    jobs = parse_umantis(UMANTIS_HTML, "https://recruitingapp-5556.de.umantis.com", org="Klinikverbund Allgäu")
    # 2464 appears twice (title + apply link, then again) -> one row; the empty anchor is skipped
    assert [j["url"].rsplit("/", 3)[1] for j in jobs] == ["2464", "577"]
    assert jobs[0]["url"] == "https://recruitingapp-5556.de.umantis.com/Vacancies/2464/Description/1"
    assert jobs[1]["title"] == "Pflegefachkraft (m/w/d) für die ZNA"          # tags stripped, entity decoded by re
    assert all(j["org"] == "Klinikverbund Allgäu" for j in jobs)


def test_parse_umantis_ignores_non_description_links():
    assert parse_umantis('<a href="/Vacancies/1/Application/CheckLogin/1">Bewerben</a>', "https://x") == []


FEED = {"dataFeedElement": [
    {"item": {"@type": "JobPosting", "title": "Pflegefachkraft (m/w/d) für die Intensivstation",
              "url": "https://josef.softgarden.io/job/45729323/x/", "datePosted": "2026-08-01",
              "employmentType": "FULL_TIME", "description": "<p>Wir <b>suchen</b> Sie</p>",
              "hiringOrganization": {"name": "Krankenhaus St. Josef"},
              "jobLocation": {"address": {"addressLocality": "Schweinfurt", "postalCode": "97421",
                                          "addressRegion": "Bayern"}}}},
    {"item": {"@type": "Organization", "name": "not a job"}},
]}


def test_parse_jobposting_feed():
    jobs = parse_jobposting_feed(FEED, "https://karriere.josef.de/jobs.feed.json")
    assert len(jobs) == 1                                     # the Organization item is skipped
    j = jobs[0]
    assert j["org"] == "Krankenhaus St. Josef"
    assert j["loc"] == [{"city": "Schweinfurt", "plz": "97421", "region": "Bayern"}]
    assert "<b>" not in j["description"] and "suchen" in j["description"]


def test_parse_jobposting_feed_accepts_type_list():
    feed = {"dataFeedElement": [{"item": {"@type": ["JobPosting"], "title": "T", "url": "u"}}]}
    assert parse_jobposting_feed(feed, "p")[0]["title"] == "T"


def test_indeed_urls_matrix_and_paging():
    urls = indeed_urls(["München"], ["pflegefachkraft"], pages=2)
    assert urls[0] == "https://de.indeed.com/jobs?q=pflegefachkraft&l=M%C3%BCnchen"
    assert urls[1].endswith("&start=10")                       # Indeed pages in steps of 10
    assert len(indeed_urls(["A", "B"], ["k1", "k2"], pages=1)) == 4


def test_js_portals_are_well_formed():
    """Every portal must declare how its list is reached, so crawl_js_portal can never guess."""
    for name, cfg in JS_PORTALS.items():
        assert cfg["kind"] in ("browser", "umantis", "feed"), name
        assert cfg.get("host"), name
        if cfg["kind"] == "umantis":
            assert cfg["base"].startswith("https://recruitingapp-"), name
        else:
            assert cfg.get("urls"), name
