# Regression test for the ats-discovery probe branch in pflege_jobs.cli._drain_once: a candidate
# careers_url that names one specific job posting must never overwrite clinics.careers_url with a
# board-shaped URL expected there -- see plan finding on cli.py's probe branch (2026-09-06).
from urllib.parse import urlparse

from pflege_jobs.cli import JOB_DETAIL_RX

# Real payload.careers_url / apply_url values measured live in pflege_jobs.inbox process_note like
# 'ats set:%' on 2026-09-06 -- job-DETAIL pages that must be rejected.
JOB_DETAIL_URLS = [
    "https://mvt-zentrum.de/job/leitung-finanzen-medizincontrolling-m-w-d/",
    "https://www.fachklinik-osterhofen.de/stellenangebot/facharzt-innere-medizin-als-oberarzt-m-w-vollzeit/",
    "https://www.helios-gesundheit.de/karriere/job/3bc89d91-7c4e-485d-ba7f-260fc7a5a378/",
    "https://gkg-bamberg.de/job/stationshilfen-m-w-in-teilzeit-oder-auf-minijob-basis/",
    "https://www.komm-ins-klinikland.de/stelle/famulatur-innere-abteilung-klinik-kitzinger-land/",
    "https://www.deutsches-herzzentrum-muenchen.de/stellenangebot/anlagenmechaniker-m-w-d-sanitaer-heizungs-und-klimatechnik--job-muenchen-107406.html",
    "https://www.josephinum.de/stellenangebot/gesundheits-und-krankenpfleger-w-m-d-fuer-schichtdienst-in-vollzeit-teilzeit/",
    "https://referral-portal-staging.lmu-klinikum.de/stellenanzeigen/personalreferent-arztliche-direktion/fa941ce576c36153",
    "https://dongku.de/stellenangebote/gku-donau-ries-kliniken-und-seniorenheime/gesundheits-und-krankenpfleger-kinderkrankenpfleger-altenpfleger-m-w-d/",
    "https://tagesklinik-westend.de/unsere-klinik/stellenangebote/assistenzarzt/",
    "https://example.de/jobs/1234-j5678.html",
    "https://example.de/Job/98765",
]

# Same live snapshot -- sane listing/board pages that must keep passing through untouched.
LISTING_URLS = [
    "https://kbo-iak.de/kbo-karriere/stellenangebote-pflege",
    "https://www.krankenhaus-st-camillus.de/stellenangebote",
    "https://www.klinik-bad-trissl.de/karriere/",
    "https://www.kreisklinik-woerth.de/stellenangebote/",
    "https://www.klinik-fraenkische-schweiz.de/herz/ueber_uns/stellenangebote",
    "https://www.klinik-angermuehle.de/jobs/",
    "https://www.waldhausklinik.de/stellenangebote-der-klinik",
    "https://www.bezirkskliniken-schwaben.de/ausbildung-karriere/stellenangebote-bewerbung",
    "https://www.muenchen-klinik.de/stellenmarkt/aktuelles-stellenangebot/",
]


def test_job_detail_urls_are_flagged():
    for url in JOB_DETAIL_URLS:
        assert JOB_DETAIL_RX.search(urlparse(url).path), f"should flag as single-posting: {url}"


def test_listing_urls_are_not_flagged():
    for url in LISTING_URLS:
        assert not JOB_DETAIL_RX.search(urlparse(url).path), f"should NOT flag as single-posting: {url}"
