"""A value the crawler substituted from the seed clinic is not evidence (2026-09-17).

Measured impact before the fix: 22 of 26 new AMEOS rows in one night carried the seed clinic's
Bavarian town ("Neuburg/Donau") while their own URL said Oberhausen / Haldensleben / Eutin, and they
passed the Bavaria gate precisely because the substituted town IS Bavarian. Across the table, 168
rows sat there as Bavarian while their URL named a non-Bavarian city, and 1438 clinic links rested
on an employer name the crawler itself had written.
"""
import csv

import pytest

from pflege_jobs.classify import norm_text
from pflege_jobs.registry import Matcher
from pflege_jobs.sources.career_crawl import city_from_url
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


@pytest.mark.parametrize("host, blocked", [
    ("referral-portal-staging.lmu-klinikum.de", True), ("jobs.staging.example.de", True),
    ("dev.klinik.de", True), ("www.lmu-klinikum.de", False), ("karriere.ameos.eu", False),
    ("stadtklinik-diako.de", False), ("bewerbung.augustinum-gruppe.de", False),
])
def test_non_production_hosts_are_recognised(host, blocked):
    assert bool(NON_PROD_HOST.search(host)) is blocked
