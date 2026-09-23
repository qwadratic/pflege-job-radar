"""TASK-99: a shared-vendor-ACCOUNT board (Artemed/SmartRecruiters, Gesundheitswelt Chiemgau) keeps each
clinic on its own distinct careers_url, so routing groups them as separate single-clinic boards -- but the
vendor's own feed returns the whole account's postings regardless of which clinic triggered the fetch.
Without widening board_clinic_ids to the full account pool, pflege_jobs.registry.Matcher._match_board
correctly refuses every posting whose city disagrees with the one triggering clinic (decision-5), so
nothing outside that one clinic can ever match -- confirmed live 2026-09-22: 0/303 jobs.smartrecruiters.com
rows matched before this fix, 261/303 after (see TASK-99's own live Matcher replay)."""
import requests

import app.crawl as CR
import crawlers.vendor_adapters as VA

ARTEMED_TRIGGER = {"clinic_id": "16228", "name": "Artemed Klinik München-Süd", "town": "München"}


def test_vendor_rows_widens_board_clinic_ids_to_the_full_account_pool(monkeypatch):
    board = {"kind": "vendor", "vendor": "smartrecruiters", "clinics": [ARTEMED_TRIGGER]}
    fake_rows = [{"kind": "jobposting", "payload": {"title": "Pflegefachkraft (m/w/d)", "url": "https://x/1"}}]
    monkeypatch.setitem(VA.VENDORS, "smartrecruiters", lambda c, session=None: list(fake_rows))
    rows = CR._vendor_rows(board, ARTEMED_TRIGGER, requests.Session(), print)
    assert rows[0]["payload"]["board_clinic_ids"] == \
        ["16228", "16235", "18105", "18802", "18808", "18813", "18872", "76108"]


def test_vendor_rows_leaves_board_clinic_ids_alone_for_a_clinic_outside_any_pool(monkeypatch):
    clinic = {"clinic_id": "99999", "name": "Not In Any Pool", "town": "X"}
    board = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [clinic]}
    monkeypatch.setitem(VA.VENDORS, "wp_jobs", lambda c, session=None: [{"kind": "jobposting", "payload": {"title": "x", "url": "https://x/1"}}])
    rows = CR._vendor_rows(board, clinic, requests.Session(), print)
    assert rows[0]["payload"]["board_clinic_ids"] == ["99999"]


# --- TASK-102: talention's own jobLocation.addressLocality mixes clean towns with facility labels ------

TALENTION_POOL = [{"clinic_id": "36301", "name": "Klinikum Weiden", "town": "Weiden"},
                   {"clinic_id": "37701", "name": "Krankenhaus Tirschenreuth", "town": "Tirschenreuth"},
                   {"clinic_id": "37703", "name": "Krankenhaus Kemnath", "town": "Kemnath"}]


def test_vendor_rows_cleans_talention_city_to_a_pool_town(monkeypatch):
    board = {"kind": "vendor", "vendor": "talention", "clinics": TALENTION_POOL}
    fake_rows = [{"kind": "jobposting", "payload": {"title": "Pflegefachkraft (m/w/d)", "url": "https://x/1",
                                                      "loc": [{"city": "Klinikum Weiden Zentrale Notaufnahme"}]}}]
    monkeypatch.setitem(VA.VENDORS, "talention", lambda c, session=None: list(fake_rows))
    rows = CR._vendor_rows(board, TALENTION_POOL[0], requests.Session(), print)
    assert rows[0]["payload"]["loc"][0]["city"] == "Weiden"


def test_vendor_rows_does_not_touch_city_for_a_non_talention_vendor(monkeypatch):
    board = {"kind": "vendor", "vendor": "wp_jobs", "clinics": TALENTION_POOL}
    fake_rows = [{"kind": "jobposting", "payload": {"title": "x", "url": "https://x/1",
                                                      "loc": [{"city": "Klinikum Weiden Zentrale Notaufnahme"}]}}]
    monkeypatch.setitem(VA.VENDORS, "wp_jobs", lambda c, session=None: list(fake_rows))
    rows = CR._vendor_rows(board, TALENTION_POOL[0], requests.Session(), print)
    assert rows[0]["payload"]["loc"][0]["city"] == "Klinikum Weiden Zentrale Notaufnahme"


# --- TASK-81 mechanism #1: la-regio-kliniken.de shares one board, no per-posting location field --------

LA_REGIO_POOL = [{"clinic_id": "26108", "name": "LA-Regio Kliniken Landshut", "town": "Landshut"},
                  {"clinic_id": "26103", "name": "Kinderkrankenhaus St. Marien Landshut", "town": "Landshut"}]


def test_vendor_rows_splits_la_regio_landshut_by_title(monkeypatch):
    board = {"kind": "vendor", "vendor": "typo3_jobs", "clinics": LA_REGIO_POOL}
    fake_rows = [{"kind": "jobposting", "payload": {"title": "Pflegefachkraft (m/w/d) Gastroenterologie", "url": "https://x/1"}},
                 {"kind": "jobposting", "payload": {"title": "Gesundheits- und Kinderkrankenpflegekräfte (w/m/d) für die Kinderchirurgie", "url": "https://x/2"}}]
    monkeypatch.setitem(VA.VENDORS, "typo3_jobs", lambda c, session=None: list(fake_rows))
    rows = CR._vendor_rows(board, LA_REGIO_POOL[0], requests.Session(), print)
    assert rows[0]["payload"]["board_clinic_ids"] == ["26108"]     # general department -> the general hospital
    assert rows[1]["payload"]["board_clinic_ids"] == ["26103"]     # pediatric title -> the children's hospital


def test_vendor_rows_does_not_narrow_an_unrelated_typo3_jobs_board(monkeypatch):
    clinic = {"clinic_id": "99999", "name": "Not La-Regio", "town": "X"}
    board = {"kind": "vendor", "vendor": "typo3_jobs", "clinics": [clinic]}
    monkeypatch.setitem(VA.VENDORS, "typo3_jobs", lambda c, session=None: [{"kind": "jobposting", "payload": {"title": "x", "url": "https://x/1"}}])
    rows = CR._vendor_rows(board, clinic, requests.Session(), print)
    assert rows[0]["payload"]["board_clinic_ids"] == ["99999"]
