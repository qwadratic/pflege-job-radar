"""TASK-170: the MEDIAN group portal, karriere.median-kliniken.de (one TYPO3 listing for ~120 facilities).

Both Bavarian MEDIAN sites had a careers_url with no job on it (RH2655: the clinic's own page on
www.median-kliniken.de, whose 6000-url sitemap names no posting; RH1480: the portal's start page). The
listing filtered to one Standort (/de/jobs/0/0/<location id>/) names every posting of that site;
crawl_wp_jobs on it also stored the unfiltered /de/jobs/ root and the canonical-link filter page as
postings, each titled with the first job of a DIFFERENT site and stamped with the seed clinic's town.
Fixtures: live 2026-09-29 -- Bad Tölz (location 62, 8 postings, one nursing), Bad Gottleuba (28, 14
postings: 10 rendered + the widget's own load-more JSON, the last page), one detail page's JSON-LD.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawlers import vendor_adapters as va  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_samples")
HOST = "https://karriere.median-kliniken.de"
TOELZ = HOST + "/de/jobs/0/0/62/"
PFK = HOST + "/de/jobs/job/Examinierte-Pflegefachkraft-mwd-de-j17969.html"
GOTTLEUBA = HOST + "/de/jobs/0/0/28/"
MORE = (HOST + "/de/jobs/?tx_medianjobportal_job_filter%5Baction%5D=more&tx_medianjobportal_job_filter%5Bcontroller%5D=Joboffer"
        "&tx_medianjobportal_job_filter%5Bgeo_lat%5D=0&tx_medianjobportal_job_filter%5Bgeo_lng%5D=0"
        "&tx_medianjobportal_job_filter%5Blocation%5D=28&tx_medianjobportal_job_filter%5Bpage%5D=2"
        "&tx_medianjobportal_job_filter%5Bradius%5D=30&tx_medianjobportal_job_filter%5Bsword%5D="
        "&tx_medianjobportal_job_filter%5Btaskareas%5D=&cHash=87d23939e0c83e9a9098f0dd33d5417c")


def _fx(name, url):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        text = f.read()
    return _R(text, url=url, ok=True, json_data=json.loads(text) if name.endswith(".json") else None)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(va.time, "sleep", lambda s: None)


def test_reads_the_standort_listing_and_each_postings_own_json_ld(monkeypatch):
    calls = []
    monkeypatch.setattr(va, "get", _router({TOELZ: _fx("median_jobs_bad_toelz_sample.html", TOELZ),
                                            PFK: _fx("median_job_j17969_sample.html", PFK)}, calls))
    rows = va.crawl_median({"clinic_id": "RH2655", "name": "MEDIAN Buchberg Klinik", "town": "Bad Tölz", "careers_url": TOELZ})
    assert [(r["payload"]["title"], r["payload"]["org"], r["payload"]["loc"][0]["city"], r["source_url"]) for r in rows] == [
        ("Examinierte Pflegefachkraft (m/w/d)", "MEDIAN Buchberg-Klinik Bad Tölz", "Bad Tölz", PFK)]
    # the other 7 postings' detail pages 404 in this test: reported, not silently dropped
    assert len(rows.page_crashes) == 7
    # only the listing and its 8 postings -- never the unfiltered /de/jobs/ root, job-alert or Initiativ page
    assert sorted(calls) == sorted([TOELZ] + [HOST + u for u in (
        "/de/jobs/job/Trainee-kaufmaennische-Leitung-Klinikmanagement-mwd-de-j18969.html",
        "/de/jobs/job/Minijob---Mitarbeiter-in-der-Waescherei-mwd-de-j18858.html",
        "/de/jobs/job/Physiotherapeut-Physiotherapeutin-mwd-klinische-Rehabilitation---Flexpool-de-j18640.html",
        "/de/jobs/job/Physiotherapeut-Physiotherapeutin-mwd-klinische-Rehabilitation---Flexpool-de-j12240.html",
        "/de/jobs/job/Ergotherapeut-mwd-de-j18800.html", "/de/jobs/job/Masseur-und-med-Bademeister-wmd-de-j16836.html",
        "/de/jobs/job/Examinierte-Pflegefachkraft-mwd-de-j17969.html", "/de/jobs/job/Physiotherapeut-mwd-de-j18403.html")])


def test_follows_the_listings_own_load_more_until_it_names_no_next_page(monkeypatch):
    calls = []
    monkeypatch.setattr(va, "get", _router({GOTTLEUBA: _fx("median_jobs_bad_gottleuba_sample.html", GOTTLEUBA),
                                            MORE: _fx("median_jobs_bad_gottleuba_more_sample.json", MORE)}, calls))
    rows = va.crawl_median({"clinic_id": "X", "name": "MEDIAN", "careers_url": GOTTLEUBA})
    detail_ids = sorted(u.rsplit("-j", 1)[1][:-5] for u in calls if "/de/jobs/job/" in u)
    assert detail_ids == sorted(["12541", "18914", "18913", "17642", "18841", "18840", "18648", "18779", "18778",
                                 "14816", "14337", "14829", "12192", "14399"])
    assert calls.count(MORE) == 1 and rows == [] and len(rows.page_crashes) == 14


def test_a_load_more_page_that_fails_raises_instead_of_reading_short(monkeypatch):
    monkeypatch.setattr(va, "get", _router({GOTTLEUBA: _fx("median_jobs_bad_gottleuba_sample.html", GOTTLEUBA)}))
    with pytest.raises(RuntimeError, match="load-more"):
        va.crawl_median({"clinic_id": "X", "name": "MEDIAN", "careers_url": GOTTLEUBA})


def test_median_is_a_routed_vendor():
    from crawlers.routing import ADAPTERS
    assert ADAPTERS["median"] == ("vendor", "crawlers.vendor_adapters:crawl_median")
    assert va.VENDORS["median"] is va.crawl_median
