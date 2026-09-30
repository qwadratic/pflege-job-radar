"""TASK-172: coveto ATS boards (kNNNNN.coveto.de).

A coveto tenant's public board /public/jobs/ is server-rendered: one row per job linking the job's own
page, and a numbered ?page=N pager. Every job page carries a schema.org JobPosting with the job's
town. That is the shape crawl_wp_jobs already reads, so routing sends the "coveto" label there. The
label had no adapter at all before: every coveto-labelled site was skipped each night ("no adapter for
coveto").

Fixtures (live 2026-09-29), Klinikum Altmühlfranken's board k61199.coveto.de, 48 jobs on two pages:
one row of page 1, two rows of page 2 (each page with its own pager), and the three jobs' JobPosting
JSON-LD (contact name and phone redacted).
"""
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.crawl as CR  # noqa: E402
from crawlers import vendor_adapters as va  # noqa: E402
from pflege_jobs.registry import Matcher  # noqa: E402
from tests.test_vendor_adapters import _R, _router  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "board_samples")
CU = "https://k61199.coveto.de/public/jobs/"
JOB = "https://k61199.coveto.de/job-%s.html"
AKAD_PFK = JOB % "akademische-pflegefachkraft-b-a-b-sc-mit-weiterbildung-praxisanleiter-in-m-w-d-weissenburg-in-bayern-1373"
PFK_CHIR = JOB % "pflegefachkraft-chirurgie-m-w-d-gunzenhausen-1370"
PFK_GERI_REHA = JOB % "pflegefachmann-frau-fuer-unsere-geriatrische-rehabilitation-m-w-d-gunzenhausen-1353"

# Live registry rows: the three sites on this board plus the other Weißenburg clinic, so the
# matcher's town checks see the real neighbourhood.
REGISTRY = [
    {"clinic_id": "57701", "name": "Klinikum Altmühlfranken Weißenburg", "town": "Weißenburg i.Bay.",
     "operator": "KU Klinikum Altmühlfranken, AöR", "beds": 190, "parse_quality": "ok"},
    {"clinic_id": "57705", "name": "Klinikum Altmühlfranken Gunzenhausen", "town": "Gunzenhausen",
     "operator": "KU Klinikum Altmühlfranken, AöR", "beds": 210, "parse_quality": "ok"},
    {"clinic_id": "RH2456", "name": "Klinikum Altmühlfranken Gunzenhausen Geriatrische Rehabilitation", "town": "Gunzenhausen",
     "operator": "Kommunalunternehmen Klinikum Altmühlfranken", "beds": 30, "parse_quality": "ok"},
    {"clinic_id": "57706", "name": "Psychiatrische Tagesklinik Weißenburg", "town": "Weißenburg i.Bay.",
     "operator": "KU Bezirkskliniken Mittelfranken, AöR", "beds": 0, "parse_quality": "ok"},
]


def _fx(name, url):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return _R(f.read(), url=url, ok=True)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(va.time, "sleep", lambda s: None)


def test_altmuehlfranken_coveto_board_is_read_across_its_pager_and_each_posting_lands_on_its_own_site(monkeypatch):
    # The board lists the town of every job ("Arbeitsort", JSON-LD addressLocality); the WordPress
    # mirror crawled before (karriere.klinikum-altmuehlfranken.de) states none, so all 13 nursing
    # postings of both houses sat on 57701 Weißenburg and 57705 Gunzenhausen / RH2456 stayed at 0.
    monkeypatch.setattr(va, "get", _router({
        CU: _fx("coveto_altmuehlfranken_jobs_sample.html", CU),
        CU + "?page=1": _fx("coveto_altmuehlfranken_jobs_sample.html", CU + "?page=1"),
        CU + "?page=2": _fx("coveto_altmuehlfranken_jobs_page2_sample.html", CU + "?page=2"),
        AKAD_PFK: _fx("coveto_altmuehlfranken_job_1373_sample.html", AKAD_PFK),
        PFK_CHIR: _fx("coveto_altmuehlfranken_job_1370_sample.html", PFK_CHIR),
        PFK_GERI_REHA: _fx("coveto_altmuehlfranken_job_1353_sample.html", PFK_GERI_REHA),
    }))
    clinics = [{**c, "careers_url": CU, "ats_type": "coveto"} for c in REGISTRY[:3]]
    board = {"kind": "vendor", "vendor": "coveto", "url": CU, "clinics": clinics}
    rows = CR._vendor_rows(board, clinics[0], requests.Session(), print)
    m = Matcher([dict(c) for c in REGISTRY])
    got = sorted((r["source_url"], r["payload"]["loc"][0]["city"],
                  (m.match(r["payload"]["org"], r["payload"]["loc"][0]["city"], board=r["payload"]["board_clinic_ids"]) or (None, None))[:2])
                 for r in rows)
    assert got == sorted([
        (AKAD_PFK, "Weißenburg in Bayern", ("57701", "R2_operator_town")),
        (PFK_CHIR, "Gunzenhausen", ("57705", "R2_operator_town")),
        (PFK_GERI_REHA, "Gunzenhausen", ("RH2456", "R1_exact")),
    ])
