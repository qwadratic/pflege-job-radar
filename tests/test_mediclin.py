"""TASK-171: MediClin career site (www.mediclin-karriere.de, TYPO3 extension "brajobsmdc").

MEDICLIN Reha-Zentrum Roter Hügel (RH2547, Bayreuth, 284 beds) lists 10 postings, 2 of them nursing
(Pflegedienstleitung; Gesundheits- und Krankenpfleger / Altenpfleger), as teaser cards whose only link
is a "zur Stellenanzeige" button on a "-wmd-" slug. crawl_wp_jobs found none of them and stored two
nav pages (/jobs/, /karrierestart/stellenangebote-fuer-nachwuchsfuehrungskraefte/) as rows instead:
0 nursing postings every night. Cards past the first 10 exist only behind the listing's own filter
form, via its AJAX endpoint (page=k, jobfilter=0).
Fixtures (live 2026-09-29): Roter Hügel's listing (10 of 10), Reha-Zentrum Gernsbach's listing (10 of
11) and its AJAX page 2 (the 11th card), one detail page (contact box left out).
"""
import json
import os
import sys
from urllib.parse import parse_qsl, urlparse

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from pflege_jobs import config as PC  # noqa: E402
from pflege_jobs.sources.inbox import jobposting_to_obs  # noqa: E402
from tests.test_vendor_adapters import _R  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_samples")
CU = "https://www.mediclin-karriere.de/reha-zentrum-roter-huegel/"
CU_G = "https://www.mediclin-karriere.de/reha-zentrum-gernsbach/"
ROTER_HUEGEL = {"clinic_id": "RH2547", "town": "Bayreuth", "careers_url": CU,
                "name": "MediClin Reha-Zentrum Roter Hügel Fachklinik für Neurologie, Orthopädie und Geriatrie"}
GERNSBACH = {"clinic_id": "X", "town": "Gernsbach", "careers_url": CU_G, "name": "MEDICLIN Reha-Zentrum Gernsbach"}


def _fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(va.time, "sleep", lambda s: None)


def _server(cu, listing, ajax_pages, calls):
    """The board as the browser sees it: the listing page, its AJAX endpoint answering by the page and
    jobfilter params it is sent, and every card's detail page (one captured detail page stands in for
    all of them; the row's title, facility and town come from the card)."""
    detail = _fx("mediclin_detail_guk_sample.html")

    def get(u, timeout=30, session=None):
        calls.append(u)
        if u == cu:
            return _R(listing, url=cu)
        if "/jobs.json?" in u:
            q = dict(parse_qsl(urlparse(u).query))
            page = ajax_pages.get((q.get("tx_brajobsmdc_jobs[page]"), q.get("tx_brajobsmdc_jobs[jobfilter]")))
            return _R(json.dumps(page), url=u, json_data=page) if page else _R(ok=False, url=u)
        if u.startswith(cu):
            return _R(detail, url=u)
        return _R(ok=False, url=u)
    return get


def _kept(rows):
    obs = [jobposting_to_obs({**r, "inbox_id": 1}, {"bayreuth", "gernsbach"}) for r in rows]
    return sorted(o["title"] for o in obs if o["role_class"] not in PC.EXCLUDED_ROLE_CLASSES)


def test_crawl_wp_jobs_reads_every_mediclin_teaser_card(monkeypatch):
    calls = []
    monkeypatch.setattr(va, "get", _server(CU, _fx("mediclin_roter_huegel_sample.html"), {}, calls))
    rows = va.crawl_wp_jobs(ROTER_HUEGEL)
    assert rows.board_total == 10                      # "1-10 von insgesamt 10 Ergebnissen"
    assert sorted(r["payload"]["title"] for r in rows) == [
        "Bundesfreiwilligendienstleistender (w/m/d)", "Diätassistent (w/m/d)", "Ergotherapeut (w/m/d)",
        "Gesundheits- und Krankenpfleger / Altenpfleger (w/m/d)", "Logopäde (w/m/d) / akadem. Sprachtherapeut (w/m/d)",
        "Masseur / Med. Bademeister (w/m/d)", "Personalsachbearbeiter (w/m/d)", "Pflegedienstleitung (w/m/d)",
        "Physiotherapeut (w/m/d)", "Sporttherapeut (w/m/d)"]
    # facility and town are stated on each card, not copied from the registry row
    assert {(r["payload"]["org"], r["payload"]["org_source"], r["payload"]["loc"][0]["city"]) for r in rows} == {
        ("MEDICLIN Reha-Zentrum Roter Hügel", None, "Bayreuth")}
    by_title = {r["payload"]["title"]: r for r in rows}
    guk = by_title["Gesundheits- und Krankenpfleger / Altenpfleger (w/m/d)"]
    assert guk["source_url"] == CU + "gesundheits-und-krankenpfleger-altenpfleger-wmd-4430-2607/"
    assert guk["payload"]["section_labels"] == ["Pflege- und Funktionsdienst"]
    assert "rehabilitativen Pflege" in guk["payload"]["description"]
    assert _kept(rows) == ["Gesundheits- und Krankenpfleger / Altenpfleger (w/m/d)", "Pflegedienstleitung (w/m/d)"]
    assert not any("sitemap" in u or u.endswith("/jobs/") for u in calls)   # the cards ARE the board


def test_mediclin_pages_through_the_listing_until_the_boards_own_total(monkeypatch):
    calls = []
    page2 = json.loads(_fx("mediclin_gernsbach_page2_sample.json"))
    monkeypatch.setattr(va, "get", _server(CU_G, _fx("mediclin_gernsbach_sample.html"), {("2", "0"): page2}, calls))
    rows = va.crawl_wp_jobs(GERNSBACH)
    assert rows.board_total == 11 and len(rows) == 11
    ajax = [dict(parse_qsl(urlparse(u).query)) for u in calls if "/jobs.json?" in u]
    assert [(q["tx_brajobsmdc_jobs[page]"], q["tx_brajobsmdc_jobs[facilityCity]"], q["tx_brajobsmdc_jobs[contentUid]"])
            for q in ajax] == [("2", "Gernsbach", "107236")]   # the form's own scope, one extra page
    last = [r for r in rows if r["source_url"] == CU_G + "freiwilligendienst-fsj-oder-bundesfreiwilligendienst-bfd-7038-3423/"]
    assert [(r["payload"]["title"], r["payload"]["loc"][0]["city"]) for r in last] == [
        ("Freiwilligendienst (FSJ) oder Bundesfreiwilligendienst (BFD)", "Gernsbach")]


def test_an_unreadable_mediclin_listing_page_raises_instead_of_reading_short(monkeypatch):
    monkeypatch.setattr(va, "get", _server(CU_G, _fx("mediclin_gernsbach_sample.html"), {}, []))
    with pytest.raises(RuntimeError, match="page 2"):
        va.crawl_wp_jobs(GERNSBACH)


def test_a_mediclin_page_that_adds_no_card_raises(monkeypatch):
    # past its last page the endpoint answers with cards already read -- never a silent short board
    listing = _fx("mediclin_gernsbach_sample.html")
    same = {"success": True, "html": [{"appendedElements": listing}]}
    monkeypatch.setattr(va, "get", _server(CU_G, listing, {("2", "0"): same}, []))
    with pytest.raises(RuntimeError, match="10 of 11"):
        va.crawl_wp_jobs(GERNSBACH)


def test_a_careers_page_without_the_brajobs_listing_is_not_mediclin():
    assert va.crawl_mediclin(ROTER_HUEGEL, cu_resp=_R("<html><a href='/jobs/x-wmd-1-2/'>x</a></html>", url=CU)) == []
