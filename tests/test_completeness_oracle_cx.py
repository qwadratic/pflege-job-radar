"""Oracle HCM "Candidate Experience" boards (jobs.sana.de, TASK-184) -- crawl_oracle reads the SPA's own REST API.

The board is a client-rendered SPA: every URL under it answers HTTP 200 with the same shell, whose <title> is "Sana".
The old path (crawl_wp_jobs through the clinics' www.sana.de pages) stored that title as the posting's text ("Sana",
4 chars), filed each posting under the seed clinic's town and name, and read nothing at all from a board URL on
jobs.sana.de itself. The shell names the REST host and site number; the list (`recruitingCEJobRequisitions`, paged to
its own TotalJobsCount) carries title, legal employer, the location hierarchy and the posting date of every requisition,
the detail (`recruitingCEJobRequisitionDetails`, one per requisition) its ad text.

tests/fixtures/board_samples/oracle_cx_sana_requisitions_sample.json: 5 real requisitions of the live site
(2026-10-01): Pegnitz, Cham (whose work-location record names Bad Kötzting), Berlin, a Bavaria-wide one placed at Land
level ("Bayern, Deutschland") and an Elmshorn one placed at country level with the town as secondary location.
No network.
"""
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from pflege_jobs.sources.inbox import jobposting_to_obs  # noqa: E402

FX = json.loads((Path(__file__).resolve().parent / "fixtures" / "board_samples" / "oracle_cx_sana_requisitions_sample.json").read_text(encoding="utf-8"))
ITEMS = FX["list_items"]
IDS = [x["Id"] for x in ITEMS]
SHELL_HTML = "<!DOCTYPE html><html><head>%s</head><body></body></html>" % FX["shell"]
API = "https://fa-eycl-saasfaeuraprod1.fa.ocs.oraclecloud.com:443/hcmRestApi/resources/latest/"
SITE = "https://jobs.sana.de/de/sites/CX_4025"
BOARD = {"name": "Sana Klinikum Neustadt - Fachklinik fuer Geriatrie und Rehabilitation", "town": "Neustadt b. Coburg",
         "careers_url": "https://jobs.sana.de/de/sites/cx_4025/jobs"}


class _R:
    def __init__(self, text="", status=200, json_data=None, url=""):
        self.text, self.status_code, self.url, self._json = text, status, url, json_data
        self.ok = 200 <= status < 300

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _Server:
    """jobs.sana.de the way the live site answers: any page under it is the SPA shell with status 200, the REST API on the
    Oracle host answers JSON. The list is served `page_size` requisitions at a time, whatever limit was asked for."""

    def __init__(self, page_size=3, detail_status=None, list_status=None):
        self.page_size, self.detail_status, self.list_status = page_size, detail_status or {}, list_status or {}
        self.calls, self.pages = [], {"https://www.sana.de/rummelsberg/karriere": _R(
            '<a href="%s/job/6544">Stationsleitung (m/w/d)</a>' % SITE, url="https://www.sana.de/rummelsberg/karriere")}

    def __call__(self, u, timeout=30, session=None):
        self.calls.append(u)
        if u.startswith(API + "recruitingCEJobRequisitions?"):
            offset = int(re.search(r"offset=(\d+)", u).group(1))
            if offset in self.list_status:
                return _R(status=self.list_status[offset], url=u)
            page = ITEMS[offset:offset + self.page_size]
            return _R(json_data={"items": [{"TotalJobsCount": len(ITEMS), "Offset": offset, "Limit": 50, "requisitionList": page}]}, url=u)
        m = re.search(r'recruitingCEJobRequisitionDetails\?.*finder=ById;Id="(\d+)",siteNumber=CX_4025', u)
        if m:
            if m.group(1) in self.detail_status:
                return _R(status=self.detail_status[m.group(1)], url=u)
            return _R(json_data={"items": [FX["details"][m.group(1)]]}, url=u)
        if u in self.pages:
            return self.pages[u]
        if u.startswith("https://jobs.sana.de/"):
            return _R(SHELL_HTML, url=u)
        return _R(status=404, url=u)

    def api_calls(self):
        return [u for u in self.calls if u.startswith(API)]


@pytest.fixture()
def server(monkeypatch):
    s = _Server()
    monkeypatch.setattr(va, "get", s)
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)
    return s


def _by_id(rows):
    return {r["payload"]["url"].rsplit("/", 1)[1]: r["payload"] for r in rows}


def test_every_requisition_the_list_reports_is_read_with_its_detail_and_the_lists_own_total_is_carried(server):
    rows = va.crawl_oracle(BOARD)
    assert [r["payload"]["url"] for r in rows] == ["%s/job/%s" % (SITE, i) for i in IDS]   # CX_4025 as the shell names it, not the board url's "cx_4025"
    assert rows.board_total == len(ITEMS) == len(rows)
    assert [r["source_host"] for r in rows] == ["jobs.sana.de"] * len(ITEMS)
    lists = [u for u in server.api_calls() if "recruitingCEJobRequisitions?" in u]
    assert [int(re.search(r"offset=(\d+)", u).group(1)) for u in lists] == [0, 3]       # pages followed until TotalJobsCount, short pages included
    assert all("expand=requisitionList.secondaryLocations" in u and "siteNumber=CX_4025" in u for u in lists)
    details = [u for u in server.api_calls() if "recruitingCEJobRequisitionDetails?" in u]
    assert sorted(re.search(r'Id="(\d+)"', u).group(1) for u in details) == sorted(IDS)  # one detail read per requisition
    assert not getattr(rows, "page_crashes", None)


def test_the_description_is_the_ads_own_text_fields_not_the_spa_title_nor_the_company_boilerplate(server):
    p = _by_id(va.crawl_oracle(BOARD))["6544"]
    assert p["description"].startswith("Attraktive Vergütung entsprechend Ihrer Qualifikation und Berufserfahrung 30 Tage Urlaub")
    assert "Fachliche, organisatorische und personelle Leitung der Station für Innere Medizin und Akutgeriatrie" in p["description"]
    assert "Abgeschlossene Ausbildung als Pflegefachkraft (m/w/d) Weiterbildung zur Stationsleitung" in p["description"]
    assert p["description"] != "Sana"
    assert "Die Sana Kliniken AG ist eine der größten Klinikgruppen" not in p["description"]      # CorporateDescriptionStr: the same on every posting
    assert "Haus der Grund- und Regelversorgung" not in p["description"]                           # OrganizationDescriptionStr: about the hospital, not the ad
    assert "Stahl" not in p["description"] and "@" not in p["description"]                         # the contact field is not part of the text


def test_title_employer_and_date_are_the_requisitions_own_not_the_seed_clinics(server):
    p = _by_id(va.crawl_oracle(BOARD))
    assert p["3084"]["title"] == "Pflegefachkraft / Pflegehilfskraft (m/w/d) für die Palliativstation / Innere Medizin"   # the source's double space folded
    assert (p["3084"]["org"], p["3084"]["org_source"]) == ("Sana Kliniken des Landkreises Cham GmbH", None)
    assert p["6544"]["org"] == "Sana Klinik Pegnitz GmbH"
    assert p["6544"]["datePosted"] == "2026-06-11"


def test_the_place_is_the_published_primary_location_at_the_level_the_source_states_it(server):
    p = _by_id(va.crawl_oracle(BOARD))
    assert p["6544"]["loc"] == [{"city": "Pegnitz", "plz": None, "region": "Bayern"}]
    # the work-location record of this requisition says "Bad Kötzting, Hauser Straße 42"; the board publishes Cham
    assert FX["details"]["3084"]["workLocation"][0]["LocationName"].startswith("Bad Kötzting")
    assert p["3084"]["loc"] == [{"city": "Cham", "plz": None, "region": "Bayern"}]
    assert p["2650"]["loc"] == [{"city": "Lichtenberg (Berlin)", "plz": None, "region": "Berlin"}]
    assert p["7276"]["loc"] == [{"city": "Bayern", "plz": None, "region": "Bayern"}]              # placed at Land level: no town level is invented
    assert p["6720"]["loc"] == [{"city": "Deutschland", "plz": None, "region": None},             # placed at country level, the town is a secondary location
                                {"city": "Elmshorn", "plz": None, "region": "Schleswig-Holstein"}]


def _obs(payload, towns=frozenset()):
    return jobposting_to_obs({"payload": payload, "source_host": "jobs.sana.de", "source_url": payload["url"], "collector": "vendor-oracle-v1",
                              "inbox_id": 1}, set(towns))


def test_the_bavaria_decision_comes_from_the_apis_own_land_not_from_a_town_list(server):
    p = _by_id(va.crawl_oracle(BOARD))
    got = {i: (o["in_bavaria"], o["city"], o["employer_name"]) for i, o in ((i, _obs(p[i])) for i in IDS)}   # towns=set(): no registry list to lean on
    assert got["6544"] == (True, "Pegnitz", "Sana Klinik Pegnitz GmbH")
    assert got["3084"] == (True, "Cham", "Sana Kliniken des Landkreises Cham GmbH")
    assert got["7276"][0] is True
    assert got["2650"][0] is False
    # a requisition placed at country level states no Land: undecided stays undecided
    assert got["6720"][0] is None


def test_a_requisition_whose_detail_cannot_be_read_is_recorded_and_left_out_not_stored_without_its_text(monkeypatch):
    s = _Server(detail_status={"3084": 500})
    monkeypatch.setattr(va, "get", s)
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)
    rows = va.crawl_oracle(BOARD)
    assert [r["payload"]["url"].rsplit("/", 1)[1] for r in rows] == ["6544", "2650", "7276", "6720"]
    assert rows.board_total == 5                                    # the caller sees 4 of 5 and records the board as incomplete
    assert [(u, "500" in why) for u, why in rows.page_crashes] == [("%s/job/3084" % SITE, True)]


def test_a_list_page_that_cannot_be_read_fails_the_board_loudly(monkeypatch):
    s = _Server(list_status={3: 503})
    monkeypatch.setattr(va, "get", s)
    monkeypatch.setattr(va.time, "sleep", lambda *a: None)
    with pytest.raises(RuntimeError, match="503"):
        va.crawl_oracle(BOARD)


def test_a_career_page_linking_into_the_site_is_read_through_the_api_too(server):
    rows = va.crawl_oracle({"name": "Krankenhaus Rummelsberg GmbH - Rehabilitation", "town": "Schwarzenbruck",
                            "careers_url": "https://www.sana.de/rummelsberg/karriere"})
    assert len(rows) == len(ITEMS) and rows.board_total == len(ITEMS)
    assert _by_id(rows)["6544"]["description"].startswith("Attraktive Vergütung")


def test_a_site_without_oracle_cx_keeps_the_existing_paths(monkeypatch):
    s = _Server()
    s.pages["https://klinikum.example.de/stellenangebote/"] = _R('<a href="/stellenangebote/a/">Pflegefachkraft (m/w/d)</a>', url="https://klinikum.example.de/stellenangebote/")
    monkeypatch.setattr(va, "get", s)
    seen = []
    monkeypatch.setattr(va, "crawl_wp_jobs", lambda c, session=None: seen.append(c["careers_url"]) or [])
    rows = va.crawl_oracle({"name": "Klinikum", "careers_url": "https://klinikum.example.de/stellenangebote/"})
    assert seen == ["https://klinikum.example.de/stellenangebote/"] and list(rows) == []
    assert s.api_calls() == []


def test_a_url_that_only_looks_like_a_cx_site_is_not_taken_for_one(monkeypatch):
    s = _Server()
    s.pages["https://example.de/de/sites/news"] = _R("<html><head><title>News</title></head></html>", url="https://example.de/de/sites/news")
    monkeypatch.setattr(va, "get", s)
    seen = []
    monkeypatch.setattr(va, "crawl_wp_jobs", lambda c, session=None: seen.append(1) or [])
    va.crawl_oracle({"name": "X", "careers_url": "https://example.de/de/sites/news/"})
    assert seen == [1] and s.api_calls() == []


def test_a_cx_site_whose_shell_cannot_be_read_fails_the_board_loudly(monkeypatch):
    monkeypatch.setattr(va, "get", lambda u, timeout=30, session=None: _R(status=503, url=u) if "/sites/" in u else _R(status=404, url=u))
    with pytest.raises(RuntimeError, match="shell not readable"):
        va.crawl_oracle(BOARD)
