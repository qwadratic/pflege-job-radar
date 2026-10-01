"""TASK-170: Deutsche Rentenversicherung Bund's own job portal, drv-bund-karriere.de.

Three Bavarian DRV Bund Reha-Zentren (Wendelstein RH1576, Hartwald RH1498, Hochstaufen RH2930) had
their own domain as careers_url -- each 302s to a www host with no DNS record at all (confirmed live
2026-09-29), so every nightly read was a transport failure. Their jobs live on the carrier's portal,
whose listing filtered by ?field_location=<id> names each job's Ort right next to its link; the
portal's own sitemap is every DRV Bund job nationwide with no location on the detail page, which
crawl_wp_jobs stamps with the seed clinic's town. Fixtures: the live Bad Brückenau listing (9 jobs,
2 nursing), its page=1 (the board's own "keine Treffer" end), and one detail page.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_samples")
CU = "https://www.drv-bund-karriere.de/jobs?search=&field_location=47"
PAGE0 = "https://www.drv-bund-karriere.de/jobs?search=&field_location=47&page=0"
PAGE1 = "https://www.drv-bund-karriere.de/jobs?search=&field_location=47&page=1"
PFK = "https://www.drv-bund-karriere.de/jobs/pflegefachkraft-bad-brueckenau-0"
HARTWALD = {"clinic_id": "RH1498", "name": "Deutsche Rentenversicherung Bund Reha-Zentrum Bad Brückenau Klinik Hartwald",
            "town": "Bad Brückenau", "careers_url": CU, "ats_type": "drv_bund"}


def _fx(name, url):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return _R(f.read(), url=url, ok=True)


def _board(extra=None):
    m = {PAGE0: _fx("drv_bund_jobs_bad_brueckenau_sample.html", PAGE0),
         PAGE1: _fx("drv_bund_jobs_bad_brueckenau_page1_sample.html", PAGE1),
         PFK: _fx("drv_bund_job_pflegefachkraft_bad_brueckenau_sample.html", PFK)}
    m.update(extra or {})
    return m


def test_reads_every_job_of_the_location_filtered_listing_with_its_own_ort(monkeypatch):
    monkeypatch.setattr(va.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(va, "get", _router(_board(), calls))
    rows = va.crawl_drv_bund(HARTWALD)
    assert sorted(r["payload"]["title"] for r in rows) == sorted([
        "Physiotherapeut*in", "Pflegefachkraft", "Psychologische*r Psychotherapeut*in", "Leitende Pflegefachkraft",
        "Oberärztin*Oberarzt (Fachärztin*Facharzt für Psychiatrie und Psychotherapie oder Fachärztin*Facharzt "
        "für Psychosomatische Medizin und Psychotherapie)",
        "Leitende*n Psychologische*n Psychotherapeut*in", "Assistenzärztin* Assistenzarzt", "Buffethilfskraft (m/w/div)",
        "Psychologische*n Psychotherapeut*in"])
    assert {r["payload"]["loc"][0]["city"] for r in rows} == {"Bad Brückenau"}
    assert {r["payload"]["org"] for r in rows} == {"Deutsche Rentenversicherung Bund"}
    assert all(r["payload"].get("city_source") is None for r in rows)   # the board's own Ort, never the seed town
    pfk = next(r for r in rows if r["source_url"] == PFK)
    assert pfk["payload"]["url"] == PFK
    assert "Reha-Zentrum Bad Brückenau Klinik Hartwald" in pfk["payload"]["description"]
    # page=1 is the board's own "keine Treffer" end signal -- asked for once, nothing past it.
    assert [u for u in calls if "/jobs?" in u] == [PAGE0, PAGE1]


def test_a_detail_page_that_fails_keeps_the_listing_row_and_is_reported(monkeypatch):
    monkeypatch.setattr(va.time, "sleep", lambda s: None)
    # 8 of the 9 detail urls are not in the mapping -> 404: the listing still names title and Ort,
    # so the row stays; the lost description is reported the same way crawl_wp_jobs reports a page crash.
    monkeypatch.setattr(va, "get", _router(_board()))
    rows = va.crawl_drv_bund(HARTWALD)
    assert len(rows) == 9
    assert len(rows.page_crashes) == 8
    assert PFK not in {u for u, _ in rows.page_crashes}


def test_a_listing_page_that_fails_mid_walk_raises_instead_of_reading_short(monkeypatch):
    monkeypatch.setattr(va, "get", _router(_board({PAGE1: _R(ok=False, url=PAGE1)})))
    with pytest.raises(RuntimeError, match="page=1"):
        va.crawl_drv_bund(HARTWALD)


def test_drv_bund_is_a_routed_vendor():
    from crawlers.routing import ADAPTERS
    assert ADAPTERS["drv_bund"] == ("vendor", "crawlers.vendor_adapters:crawl_drv_bund")
    assert va.VENDORS["drv_bund"] is va.crawl_drv_bund


# The Bavarian DRV Baden-Württemberg house, Rehaklinik Am Kurpark (RH2467, Bad Kissingen), posts on its
# carrier's b-ite board under the employer "RehaZentren der Deutschen Rentenversicherung" (live
# 2026-09-29: Pflegefachkraft + Pflegedienstleitung). Its name tokens tie two DRV BUND houses in the
# same town (Klinik Saale RH1901, Klinik Rhön RH2954: "deutschen rentenversicherung"), and that tie's
# bed-count guess (R6) returned before the operator tokens -- which name RH2467 alone -- were ever read.
def test_a_drv_bw_posting_in_bad_kissingen_reaches_its_own_house_not_a_drv_bund_neighbour():
    from pflege_jobs.registry import Matcher
    town = "Bad Kissingen"
    bw = "Reha Zentren der Deutschen Rentenversicherung Baden Württemberg gGmbH"
    rows = [
        {"clinic_id": "RH1901", "town": town, "beds": 190, "operator": "Deutsche Rentenversicherung Bund",
         "name": "Reha-Zentrum Bad Kissingen Klinik Saale der Deutschen Rentenversicherung Bund"},
        {"clinic_id": "RH2954", "town": town, "beds": 160, "operator": "Deutsche Rentenversicherung Bund",
         "name": "Reha-Zentrum Bad Kissingen Klinik Rhön der Deutschen Rentenversicherung Bund"},
        {"clinic_id": "RH2467", "town": town, "beds": 183, "name": "Rehaklinik Am Kurpark", "operator": bw},
        {"clinic_id": "RH1775", "town": town, "beds": 145, "operator": "Deutsche Rentenversicherung Nordbayern",
         "name": "Frankenklinik Deutsche Rentenversicherung Nordbayern"}]
    m = Matcher(rows)
    assert m.match("RehaZentren der Deutschen Rentenversicherung", town, board=["RH2467"]) == ("RH2467", "R4_tokens_op", 0.75)
    # a posting that names a DRV Bund house ties the two Bund houses on BOTH rungs: the text has to name the one it is
    # (until 2026-10-01 their bed-count guess, RH1901, answered for every such ad)
    notes = []
    assert m.match("Reha-Zentrum Bad Kissingen der Deutschen Rentenversicherung Bund", town, note=notes) is None
    assert notes == ["R6 refused: sites RH1901,RH2954 tie and the text names none of them"]
    assert m.match("Reha-Zentrum Bad Kissingen der Deutschen Rentenversicherung Bund", town, title="Pflegefachkraft (m/w/d) in der Klinik Rhön") == \
        ("RH2954", "R6_ambiguous_sites:RH1901,RH2954", 0.5)
    # when the operator rung ties too (a second, hypothetical DRV BW house in town), the text has to name the house there as well
    two_bw = Matcher(rows + [{"clinic_id": "X", "town": town, "beds": 300, "name": "Rehaklinik Am Park", "operator": bw}])
    assert two_bw.match("RehaZentren der Deutschen Rentenversicherung", town) is None
    assert two_bw.match("RehaZentren der Deutschen Rentenversicherung", town, title="Pflegefachkraft (m/w/d) Rehaklinik Am Park") == \
        ("X", "R6_ambiguous_sites:RH2467,X", 0.5)
