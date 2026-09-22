"""A value the crawler substituted from the seed clinic is not evidence (2026-09-17).

Measured impact before the fix: 22 of 26 new AMEOS rows in one night carried the seed clinic's
Bavarian town ("Neuburg/Donau") while their own URL said Oberhausen / Haldensleben / Eutin, and they
passed the Bavaria gate precisely because the substituted town IS Bavarian. Across the table, 168
rows sat there as Bavarian while their URL named a non-Bavarian city, and 1438 clinic links rested
on an employer name the crawler itself had written.
"""
import csv

import pytest

from crawlers.vendor_adapters import parse_job_page
from pflege_jobs.classify import norm_text
from pflege_jobs.registry import Matcher
from pflege_jobs.sources.career_crawl import Crawler, city_from_url
from pflege_jobs.sources.inbox import NON_PROD_HOST, jobposting_to_obs

TOWNS = {norm_text(r["town"]) for r in csv.DictReader(open("data/registry/clinics.csv", encoding="utf-8")) if r.get("town")}


def _obs(url, city, org, city_source=None, org_source=None):
    payload = {"title": "Pflegefachkraft (m/w/d)", "org": org, "url": url, "description": "",
               "loc": [{"city": city, "plz": None, "region": None}]}
    if city_source:
        payload["city_source"] = city_source
    if org_source:
        payload["org_source"] = org_source
    return jobposting_to_obs({"inbox_id": 1, "source_host": "karriere.ameos.eu", "source_url": url,
                              "collector": "vendor-wp_jobs-v1", "payload": payload}, TOWNS)


@pytest.mark.parametrize("url, expected", [
    ("https://x/10240-pflegefachkraft-in-oberhausen", "oberhausen"),
    ("https://x/2-pflege-in-halberstadt.html", "halberstadt"),
    ("https://x/1-pfk-in-neuburg-an-der-donau", "neuburg an der donau"),   # registry spells it Neuburg/Donau
    ("https://x/4-pfk-in-garmisch-partenkirchen", "garmisch partenkirchen"),
    ("https://x/3-pfk-m-w-d-in-teilzeit", None),                            # not a city
    ("https://x/5-pfk-in-vollzeit/", None),
    ("https://x/6-ota-m-w-d", None),                                        # no city in the url at all
])
def test_city_from_url_only_returns_placeable_cities(url, expected):
    assert city_from_url(url, TOWNS) == expected


def test_url_city_overrides_an_inherited_one_and_the_row_is_dropped():
    o = _obs("https://karriere.ameos.eu/stelle/10240-pflegefachkraft-in-oberhausen",
             city="Neuburg/Donau", org="AMEOS Klinikum Neuburg", city_source="seed")
    assert o["city"] == "oberhausen" and o["in_bavaria"] is False        # intake drops in_bavaria False


def test_a_genuine_bavarian_row_is_untouched():
    o = _obs("https://karriere.ameos.eu/stelle/99-pflegefachkraft-in-neuburg-an-der-donau",
             city="Neuburg/Donau", org="AMEOS Klinikum Neuburg", city_source="seed")
    assert o["city"] == "Neuburg/Donau" and o["in_bavaria"] is True

    o2 = _obs("https://x.de/job/pflegefachkraft-m-w-d-in-teilzeit", city="Fürth", org="Klinikum Fürth")
    assert o2["city"] == "Fürth" and o2["in_bavaria"] is True


def test_matcher_refuses_a_clinic_matched_by_its_own_inherited_name():
    cl = [{"clinic_id": "18501", "name": "AMEOS Klinikum St. Elisabeth Neuburg", "town": "Neuburg/Donau", "operator": "AMEOS", "beds": 200},
          {"clinic_id": "27706", "name": "AMEOS Klinikum Inntal", "town": "Haag", "operator": "AMEOS", "beds": 100}]
    m = Matcher(cl)
    name = "AMEOS Klinikum St. Elisabeth Neuburg"
    assert m.match(name, "Oberhausen")[1] == "R1_exact"                  # read off the page: still trusted
    assert m.match(name, "Oberhausen", employer_inherited=True) is None  # circular: refused
    assert m.match(name, "Neuburg/Donau", employer_inherited=True)[0] == "18501"   # earns it from the city


def test_parse_job_page_marks_org_source_seed_only_when_the_page_states_no_employer():
    # No hiringOrganization anywhere on the page -> the caller's seed clinic name is a guess.
    html_no_org = '<h1>Pflegefachkraft (m/w/d)</h1><span class="fact">Musterstadt</span>'
    j = parse_job_page(html_no_org, "https://x/1", "Seed Klinik GmbH")
    assert j["org"] == "Seed Klinik GmbH" and j.get("org_source") == "seed"
    # A real hiringOrganization on the page must win over the seed guess and NOT be marked inherited.
    html_with_org = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Pflegefachkraft (m/w/d)",'
                      '"hiringOrganization":{"name":"Sibling Klinik Penzberg"},'
                      '"jobLocation":{"address":{"addressLocality":"Penzberg"}}}</script>')
    j2 = parse_job_page(html_with_org, "https://x/2", "Seed Klinik GmbH")
    assert j2["org"] == "Sibling Klinik Penzberg" and j2.get("org_source") is None


def test_real_producer_output_drives_matcher_to_the_sibling_not_the_seed():
    """End to end, no hand-set flags: crawlers.vendor_adapters.parse_job_page's own org_source ->
    pflege_jobs.sources.inbox.jobposting_to_obs's _emp_inherited -> Matcher.match refuses the seed
    clinic and lets the posting's own city earn it the sibling clinic instead, on a board shared by
    both (mirrors the Starnberger/Heiligenfeld shared-softgarden-board shape from the 2026-09-18
    crawler review)."""
    cl = [{"clinic_id": "19001", "name": "Klinikum Seedstadt", "town": "Seedstadt", "operator": None, "beds": 200},
          {"clinic_id": "19003", "name": "Klinik Penzberg", "town": "Penzberg", "operator": None, "beds": 80}]
    m = Matcher(cl)
    html_with_org = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Pflegefachkraft (m/w/d)",'
                      '"hiringOrganization":{"name":"Klinik Penzberg"},'
                      '"jobLocation":{"address":{"addressLocality":"Penzberg"}}}</script>')
    j = parse_job_page(html_with_org, "https://board.example/job/1", "Klinikum Seedstadt")
    payload = {"title": j["title"], "org": j["org"], "org_source": j.get("org_source"), "url": j["url"],
               "description": "", "loc": j["loc"], "board_clinic_ids": ["19001", "19003"]}
    o = jobposting_to_obs({"inbox_id": 1, "source_host": "board.example", "source_url": j["url"],
                           "collector": "vendor-wp_jobs-v1", "payload": payload}, TOWNS | {"seedstadt", "penzberg"})
    assert o["_emp_inherited"] is False   # the page named a real employer, not the seed clinic
    mt = m.match(o["employer_name"], o["city"], board=o["_board"], employer_inherited=o["_emp_inherited"])
    assert mt[0] == "19003"   # Klinik Penzberg, not the seed board clinic 19001


def test_employer_inherited_from_real_pipeline_suppresses_r1_r2_on_a_shared_board():
    """Same real pipeline as the sibling test above (crawlers.vendor_adapters.parse_job_page ->
    pflege_jobs.sources.inbox.jobposting_to_obs -> Matcher.match, no hand-set flag), but for the
    mirror case: the page states NO hiringOrganization at all, so org falls back to the seed
    clinic's own registry name -- exactly the AMEOS shape (every posting on a shared board gets the
    seed clinic's name verbatim). Without employer_inherited suppressing R1/R2, that inherited name
    trivially R1-exact-matches the seed clinic on every posting regardless of the posting's own
    city; with it, the posting must instead earn its clinic from the board+city (R0_board_town)."""
    cl = [{"clinic_id": "19001", "name": "Klinikum Seedstadt", "town": "Seedstadt", "operator": None, "beds": 200},
          {"clinic_id": "19003", "name": "Klinik Penzberg", "town": "Penzberg", "operator": None, "beds": 80}]
    m = Matcher(cl)
    html_no_org = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Pflegefachkraft (m/w/d)",'
                   '"jobLocation":{"address":{"addressLocality":"Penzberg"}}}</script>')
    j = parse_job_page(html_no_org, "https://board.example/job/9", "Klinikum Seedstadt")
    assert j["org"] == "Klinikum Seedstadt" and j["org_source"] == "seed"   # the seed guess, unlabelled
    payload = {"title": j["title"], "org": j["org"], "org_source": j.get("org_source"), "url": j["url"],
               "description": "", "loc": j["loc"], "board_clinic_ids": ["19001", "19003"]}
    o = jobposting_to_obs({"inbox_id": 1, "source_host": "board.example", "source_url": j["url"],
                           "collector": "vendor-wp_jobs-v1", "payload": payload}, TOWNS | {"seedstadt", "penzberg"})
    assert o["_emp_inherited"] is True and o["employer_name"] == "Klinikum Seedstadt"

    mt = m.match(o["employer_name"], o["city"], board=o["_board"], employer_inherited=o["_emp_inherited"])
    assert mt is not None and mt[0] == "19003" and mt[1] == "R0_board_town"   # earned via city, not name

    # Sanity check on the same fixture, updated by TASK-81 AC#2 (2026-09-21): R1_exact is now gated
    # on town even for a genuinely-read (non-inherited) name, the same as its R1_exact_town sibling
    # always was -- verified correct on live production data, structurally identical to this fixture
    # (employer text names one specific site, the posting's own city names a real sibling on the
    # same board): "RoMed Klinikum Rosenheim" / city "Bad Aibling", posting_id 6421, used to
    # R1_exact-match RoMed's Rosenheim site and now correctly falls through to the Bad Aibling
    # sibling the city actually names. So here too: city "Penzberg" names a real OTHER registry site
    # (19003), not just any string, so R1_exact refuses and the board rules correctly hand it to
    # 19003 instead -- same result as the `employer_inherited=True` branch above, for a different
    # reason (this diff's docstring point -- "a value the crawler substituted is not evidence" --
    # still holds; a value the crawler substituted was never the only thing that could be wrong).
    assert m.match(o["employer_name"], o["city"], board=o["_board"], employer_inherited=False) == ("19003", "R0_board_town", 0.85)


def test_career_crawl_from_jsonld_trusts_the_posting_own_hiring_organization():
    """Same shape, on the career_crawl (softgarden/umantis) seeded-adapter path, which skips
    inbox.py entirely and hands observations straight to app.crawl._load_observations."""
    towns = TOWNS | {"seedstadt", "penzberg"}
    cr = Crawler(towns)
    seed = {"name": "Klinikum Seedstadt", "kez": "19001", "town": "Seedstadt", "career": "https://board.example/de/vacancies"}
    jp_with_org = {"title": "Pflegefachkraft (m/w/d)", "hiringOrganization": {"name": "Klinik Penzberg"},
                   "jobLocation": {"address": {"addressLocality": "Penzberg"}}}
    o = cr._from_jsonld(jp_with_org, "https://board.example/job/1", seed)
    assert o["employer_name"] == "Klinik Penzberg" and o["_emp_inherited"] is False

    jp_no_org = {"title": "Pflegefachkraft (m/w/d)", "jobLocation": {"address": {"addressLocality": "Penzberg"}}}
    o2 = cr._from_jsonld(jp_no_org, "https://board.example/job/2", seed)
    assert o2["employer_name"] == "Klinikum Seedstadt" and o2["_emp_inherited"] is True


@pytest.mark.parametrize("host, blocked", [
    ("referral-portal-staging.lmu-klinikum.de", True), ("jobs.staging.example.de", True),
    ("dev.klinik.de", True), ("www.lmu-klinikum.de", False), ("karriere.ameos.eu", False),
    ("stadtklinik-diako.de", False), ("bewerbung.augustinum-gruppe.de", False),
])
def test_non_production_hosts_are_recognised(host, blocked):
    assert bool(NON_PROD_HOST.search(host)) is blocked
