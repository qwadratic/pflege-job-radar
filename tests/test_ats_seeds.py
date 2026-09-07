"""Umantis seed-builder fallbacks in pflege_jobs.sources.ats_seeds. Mocked HTTP, no network."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pflege_jobs.sources import ats_seeds  # noqa: E402


class _Resp:
    def __init__(self, text):
        self.text = text


def test_umantis_host_is_the_board_itself(monkeypatch):
    # https://recruitingapp-5545.de.umantis.com/Jobs/1?lang=ger has zero absolute umantis URLs in
    # its own HTML (relative /Vacancies/<id>/Description/1 links only) -- must not need a fetch.
    calls = []
    monkeypatch.setattr(ats_seeds, "_get", lambda u: calls.append(u) or _Resp(""))
    f = {"name": "Medic-Center Fürth", "career": "https://recruitingapp-5545.de.umantis.com/Jobs/1?lang=ger"}
    seed = ats_seeds.umantis(f, "56304", "Fürth")
    assert seed is not None
    assert seed["career"] == "https://recruitingapp-5545.de.umantis.com/Jobs/1?lang=ger"
    assert seed["hosts"] == ["recruitingapp-5545.de.umantis.com"]
    assert seed["ats"] == "umantis"
    assert not calls  # no network needed when careers_url is already the umantis host


def test_umantis_one_hop_hub_page(monkeypatch):
    # ANregiomed's careers_url is a CMS hub with no umantis URL; the real listing page is one
    # click away and carries an absolute recruitingapp-5511 URL.
    hub_html = '<a href="/karriere-jobs/stellenangebote-bewerbung/stellenangebote/">Stellenangebote</a>'
    listing_html = '<a href="https://recruitingapp-5511.de.umantis.com/Vacancies/1/Description/1">Job</a>'

    def fake_get(url):
        if url == "https://www.anregiomed.de/karriere-jobs/":
            return _Resp(hub_html)
        if url == "https://www.anregiomed.de/karriere-jobs/stellenangebote-bewerbung/stellenangebote/":
            return _Resp(listing_html)
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(ats_seeds, "_get", fake_get)
    f = {"name": "ANregiomed Klinikum Ansbach", "career": "https://www.anregiomed.de/karriere-jobs/"}
    seed = ats_seeds.umantis(f, "56101", "Ansbach")
    assert seed is not None
    assert seed["career"] == "https://recruitingapp-5511.de.umantis.com/Jobs/1"
    assert seed["hosts"] == ["recruitingapp-5511.de.umantis.com"]


def test_umantis_none_when_no_umantis_url_anywhere(monkeypatch):
    monkeypatch.setattr(ats_seeds, "_get", lambda u: _Resp("<html>no jobs here</html>"))
    f = {"name": "Nowhere Klinik", "career": "https://example.com/karriere"}
    assert ats_seeds.umantis(f, "1", "Nowhere") is None


def test_umantis_does_not_append_a_category_param_no_working_filter_exists(monkeypatch):
    # Checked live 2026-09-07: recruitingapp-5545.de.umantis.com renders a real "Unternehmensbereich"
    # job-function facet (searchFunction=10020 -> "Pflegedienst"), but it is POST-only -- GET query-
    # param variants (?searchFunction=10020 etc.) came back byte-for-byte the same as the unfiltered
    # page. So umantis() must keep building the plain board URL (Jobs/1, no filter param); the
    # generic BFS in career_crawl.Crawler is what gets a shot at a section link on the rendered page.
    calls = []
    monkeypatch.setattr(ats_seeds, "_get", lambda u: calls.append(u) or _Resp(""))
    f = {"name": "kbo-Heckscher-Klinikum München", "career": "https://recruitingapp-5545.de.umantis.com/Jobs/1?lang=ger&ContentOnly=&message="}
    seed = ats_seeds.umantis(f, "16212", "München")
    assert seed is not None
    assert "searchFunction" not in seed["career"]
    assert all("searchFunction" not in u for u in seed["extra_seeds"])
