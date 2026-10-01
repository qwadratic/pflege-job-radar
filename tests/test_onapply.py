"""TASK-170: onapply career-page widget (Klinik Höhenried RH2229, DRV Bayern Süd, 524 beds).

hoehenried.de/home/karriere/stellenangebote/ renders no job link at all: two empty
<div class="onapply-career-page-container" data-url="https://<tenant>.onapply.de/feed/render.html?format=json">
containers are filled client-side from that JSON feed, so crawl_wp_jobs read 0 rows every night.
Fixtures (live 2026-09-29): the two containers, both feeds (13 + 1 listings) and one detail page's
JobPosting JSON-LD (the one nursing listing -- an Initiativbewerbung; the posting itself says every
position is filled, so the classifier's speculative-application rule correctly keeps it off the board).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_samples")
CU = "https://hoehenried.de/home/karriere/stellenangebote/"
FEED = "https://hoehenried.onapply.de/feed/render.html?format=json"
FEED_CEP = "https://cep-hoehenried.onapply.de/feed/render.html?format=json"
PFK = "https://hoehenried.onapply.de/details/90859.html"
HOEHENRIED = {"clinic_id": "RH2229", "name": "Deutsche Rentenversicherung Bayern Süd Klinik Höhenried gGmbH",
              "town": "Bernried/Obb.", "careers_url": CU}


def _fx(name, url):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        text = f.read()
    return _R(text, url=url, ok=True, json_data=json.loads(text) if name.endswith(".json") else None)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(va.time, "sleep", lambda s: None)


def _board():
    return {CU: _fx("onapply_hoehenried_stellenangebote_sample.html", CU),
            FEED: _fx("onapply_hoehenried_feed_sample.json", FEED),
            FEED_CEP: _fx("onapply_cep_hoehenried_feed_sample.json", FEED_CEP),
            PFK: _fx("onapply_hoehenried_detail_90859_sample.html", PFK)}


def test_crawl_wp_jobs_reads_both_onapply_feeds_behind_the_widget(monkeypatch):
    calls = []
    monkeypatch.setattr(va, "get", _router(_board(), calls))
    rows = va.crawl_wp_jobs(HOEHENRIED)
    assert rows.board_total == 14          # 13 + 1 listings: the two feeds ARE the board
    assert [(r["payload"]["title"], r["payload"]["org"], r["payload"]["loc"][0]["city"], r["payload"]["section_labels"])
            for r in rows] == [("Initiativbewerbung Pflegefachkraft (m/w/d)", "Klinik Höhenried gGmbH",
                                "Bernried am Starnberger See", ["Pflege"])]
    # every listing's own detail page was asked for (13 of them 404 here -> fewer rows than board_total,
    # which app/crawl.py records as an incomplete read)
    assert len({u for u in calls if "/details/" in u}) == 14
    assert not any("sitemap" in u for u in calls)   # the board is the feeds; no generic walk after them


def test_a_careers_page_without_the_widget_is_not_onapply(monkeypatch):
    monkeypatch.setattr(va, "get", _router({CU: _R("<html><a href='/x'>x</a></html>", url=CU)}))
    assert va.crawl_onapply(HOEHENRIED, cu_resp=va.get(CU)) == []


def test_an_unreadable_feed_raises_instead_of_reading_short(monkeypatch):
    b = _board()
    b[FEED_CEP] = _R(ok=False, url=FEED_CEP)
    monkeypatch.setattr(va, "get", _router(b))
    with pytest.raises(RuntimeError, match="cep-hoehenried"):
        va.crawl_wp_jobs(HOEHENRIED)


def test_a_hoehenried_posting_city_reaches_its_registry_row():
    # Every Höhenried listing names the municipality "Bernried am Starnberger See"; the registry (RHV)
    # spells the town "Bernried/Obb." -- keyed apart, the single-clinic board refused every posting.
    from pflege_jobs.registry import Matcher
    m = Matcher([{"clinic_id": "RH2229", "name": "Deutsche Rentenversicherung Bayern Süd Klinik Höhenried gGmbH",
                  "town": "Bernried/Obb.", "operator": "Klinik Höhenried gGmbH der DRV Bayern Süd", "beds": 524}])
    assert m.match("Klinik Höhenried gGmbH", "Bernried am Starnberger See", board=["RH2229"])[0] == "RH2229"


def test_grounds_keeping_titles_are_not_nursing():
    # "pflege" inside a grounds-keeping compound is not care work: the Höhenried feed's gardener listing,
    # plus the four live open postings (14939, 15043, 15044, 15046) this same gap filed as sonstige_pflege.
    from pflege_jobs.classify import classify_role
    for title in ["Mitarbeiter in der Parkpflege m/w/d)", "Mitarbeiter Gartenpflege / Grünflächenpflege (w/m/d)",
                  "LKW-Fahrer (m/w/d) in der Landschaftspflege", "Maschinenführer (m/w/d) in der Landschaftspflege",
                  "Fachbereichskoordinator Grünanlagenpflege (m/w/d)"]:
        assert classify_role(title)[0] == "nicht_pflege", title
    assert classify_role("Pflegefachkraft (m/w/d) für die Geriatrie")[0] == "pflegefachkraft"
