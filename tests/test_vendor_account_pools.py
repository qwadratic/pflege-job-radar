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


# --- TASK-118: meinkrankenhaus2030.de shares one board (19001 Schongau / 19002 Weilheim), no ------
# structured location field at all -- a clinic-scoped crawl of just one of the two never includes
# its sibling in board.clinics, so VA.account_pool_for widens board_clinic_ids the same way TASK-99's
# pools do; the row's own city, when the board states it in plain "am Standort <Ort>" prose, must
# also stop being silently overwritten by the single triggering clinic's seed town.

WEILHEIM_TRIGGER = {"clinic_id": "19001", "name": "Krankenhaus Schongau", "town": "Schongau"}
BAVARIA_TOWNS = {"schongau", "weilheim", "münchen"}


def test_vendor_rows_reads_a_real_standort_instead_of_stamping_the_seed_town(monkeypatch):
    board = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [WEILHEIM_TRIGGER]}  # clinic-scoped: 19002 not in the list
    fake_rows = [{"kind": "jobposting", "payload": {
        "title": "Operations-Technischen-Assistent (w/m/d)", "url": "https://x/1",
        "description": "Für unsere OP-Abteilung am Standort Weilheim suchen wir Verstärkung."}}]
    monkeypatch.setitem(VA.VENDORS, "wp_jobs", lambda c, session=None: list(fake_rows))
    rows = CR._vendor_rows(board, WEILHEIM_TRIGGER, requests.Session(), print, towns=BAVARIA_TOWNS)
    assert rows[0]["payload"]["board_clinic_ids"] == ["19001", "19002"]  # widened by account_pool_for
    assert rows[0]["payload"]["loc"][0]["city"] == "Weilheim"            # read from the page, not seed-stamped
    assert rows[0]["payload"].get("city_source") is None                # real page data, not city_source="seed"


def test_vendor_rows_leaves_city_unset_when_the_pool_has_no_standort_and_more_than_one_town(monkeypatch):
    # Unchanged pre-existing behaviour (same as the Artemed/SmartRecruiters 8-clinic pool): the
    # single-clinic seed-town fallback only ever applied when len(ids) == 1 -- a row with no real
    # location signal on a WIDENED multi-clinic pool stays without a city, rather than confidently
    # (and, for this exact pair, often wrongly) stamping whichever clinic happened to trigger the
    # fetch. board_clinic_ids is still widened -- that part is unconditional -- only the per-row city
    # default is gated on pool size, same as before this task.
    board = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [WEILHEIM_TRIGGER]}
    fake_rows = [{"kind": "jobposting", "payload": {
        "title": "Pflegefachkraft (m/w/d)", "url": "https://x/1", "description": "Wir suchen Verstärkung für unser Team."}}]
    monkeypatch.setitem(VA.VENDORS, "wp_jobs", lambda c, session=None: list(fake_rows))
    rows = CR._vendor_rows(board, WEILHEIM_TRIGGER, requests.Session(), print, towns=BAVARIA_TOWNS)
    assert rows[0]["payload"]["board_clinic_ids"] == ["19001", "19002"]  # still widened -- the pool itself is unconditional
    assert "loc" not in rows[0]["payload"]                              # no real signal, no pool-size-1 fallback either


def test_vendor_rows_still_falls_back_to_seed_town_for_a_single_clinic_board(monkeypatch):
    clinic = {"clinic_id": "99999", "name": "Not In Any Pool", "town": "Regensburg"}
    board = {"kind": "vendor", "vendor": "wp_jobs", "clinics": [clinic]}
    fake_rows = [{"kind": "jobposting", "payload": {"title": "Pflegefachkraft (m/w/d)", "url": "https://x/1"}}]
    monkeypatch.setitem(VA.VENDORS, "wp_jobs", lambda c, session=None: list(fake_rows))
    rows = CR._vendor_rows(board, clinic, requests.Session(), print, towns=BAVARIA_TOWNS)
    assert rows[0]["payload"]["board_clinic_ids"] == ["99999"]          # no pool, unwidened
    assert rows[0]["payload"]["loc"][0]["city"] == "Regensburg"         # no real signal, but a lone clinic -> seed fallback stands
    assert rows[0]["payload"]["city_source"] == "seed"
