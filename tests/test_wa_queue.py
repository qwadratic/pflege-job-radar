"""Offline tests for app/wa/queue.py (card_to_candidate, build_queue_entry) and the two new
GET /api/wa/queue* endpoints (app/wa/queue_api.py). No real CLI, no real network.

The endpoint handlers are called directly here (not through a TestClient) since both routes are
owner-only (app/auth.py:OWNER_READ_PREFIXES) and take no request-scoped arguments -- auth gating
itself is covered by tests/test_auth.py's own DENIED-route table, which this task adds both new
paths to, rather than duplicating a login flow in this file."""
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import queue as Q
from app.wa import queue_api as QA


def _clinics():
    return [{"clinic_id": "c1", "name": "Klinikum München", "town": "München",
             "regierungsbezirk": "Oberbayern", "beds": 800, "jobs_open": 1, "fachrichtungen": []},
            {"clinic_id": "c2", "name": "Klinikum Augsburg", "town": "Augsburg",
             "regierungsbezirk": "Schwaben", "beds": 600, "jobs_open": 1, "fachrichtungen": []}]


def _jobs():
    return [{"posting_id": 1, "clinic_id": "c1", "role_class": "pflegefachkraft",
             "department_hint": "Intensiv/IMC", "qualification_hint": None, "title": "Pflegefachkraft Intensiv"},
            {"posting_id": 2, "clinic_id": "c2", "role_class": "pflegefachkraft",
             "department_hint": "Innere Medizin", "qualification_hint": None, "title": "Pflegefachkraft Innere"}]


@pytest.fixture()
def board(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": _jobs(), "clinics": _clinics(),
                    "by_clinic": {c["clinic_id"]: c for c in _clinics()}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")


def test_card_to_candidate_derives_region_from_city_not_the_cards_own_region_field(board):
    """card['region'] holds the out-of-scope-Bundesland gate value (e.g. 'hessen'), never a
    Regierungsbezirk -- it must never leak into the candidate dict matching.score() reads."""
    card = {"qualification_path": "urkunde", "city": "München", "region": "hessen", "department_pref": "Intensiv/IMC"}
    cand = Q.card_to_candidate(card)
    assert cand["region"] == "Oberbayern"
    assert cand["role_class"] == "pflegefachkraft"
    assert cand["departments"] == ["Intensiv/IMC"]
    assert cand["anerkennung_status"] == "granted"


def test_card_to_candidate_falls_back_to_cv_profile_when_the_card_has_no_city(board):
    card = {"qualification_path": "defizit"}
    cv_profile = {"cities": ["Augsburg"], "departments": ["Innere Medizin"], "qualifications": ["GuK"],
                  "languages": ["Deutsch B2"], "roles": ["pflegefachkraft"]}
    cand = Q.card_to_candidate(card, cv_profile)
    assert cand["city"] == "Augsburg"
    assert cand["region"] == "Schwaben"
    assert cand["german_level"] == "B2"
    assert cand["anerkennung_status"] == "deficit_notice"
    assert "GuK" in cand["qualification"]


def test_build_queue_entry_ranks_against_the_live_snapshot_and_stores_both_tables(board):
    card = {"qualification_path": "urkunde", "city": "München", "department_pref": "Intensiv/IMC"}
    out = Q.build_queue_entry("+491234567890", card)
    assert out["matches"], "at least one clinic should rank for a plain qualified München candidate"
    assert out["matches"][0]["clinic_id"] == "c1"

    conn = Q.db()
    try:
        cand_row = conn.execute("select * from wa_queue_candidates where phone=?", ("+491234567890",)).fetchone()
        assert cand_row is not None
        match_rows = conn.execute("select * from wa_queue_matches where phone=?", ("+491234567890",)).fetchall()
        assert len(match_rows) == len(out["matches"])
    finally:
        conn.close()


def test_build_queue_entry_upserts_on_repeat_consent_not_duplicate_rows(board):
    card = {"qualification_path": "urkunde", "city": "München", "department_pref": "Intensiv/IMC"}
    Q.build_queue_entry("+491111111111", card)
    Q.build_queue_entry("+491111111111", card)   # a second consent, or a later CV upload re-triggering this
    conn = Q.db()
    try:
        cand_rows = conn.execute("select * from wa_queue_candidates where phone=?", ("+491111111111",)).fetchall()
        assert len(cand_rows) == 1, "must upsert, not duplicate, the candidate row"
        match_rows = conn.execute("select * from wa_queue_matches where phone=?", ("+491111111111",)).fetchall()
        assert len(match_rows) == len({(m["clinic_id"]) for m in match_rows}), "no duplicate (phone, clinic_id, posting_id) rows"
    finally:
        conn.close()


def test_build_queue_entry_resolves_a_known_contact(board):
    from app.wa.luna import contacts as CT
    conn = CT.db()
    CT.save_contact(conn, "c1", "pd@klinikum-muenchen.example", "board", "high")
    conn.close()

    card = {"qualification_path": "urkunde", "city": "München", "department_pref": "Intensiv/IMC"}
    out = Q.build_queue_entry("+491234567891", card)
    c1_match = next(m for m in out["matches"] if m["clinic_id"] == "c1")
    conn = Q.db()
    try:
        row = conn.execute("select contact_email from wa_queue_matches where phone=? and clinic_id=?",
                           ("+491234567891", "c1")).fetchone()
        assert row["contact_email"] == "pd@klinikum-muenchen.example"
    finally:
        conn.close()


# --- endpoints -----------------------------------------------------------------------------------

def test_queue_endpoint_lists_candidates_with_matches(board):
    Q.build_queue_entry("+491234567892", {"qualification_path": "urkunde", "city": "München", "department_pref": "Intensiv/IMC"})
    body = QA.wa_queue()
    assert body["total"] >= 1
    assert any(row["phone"] == "+491234567892" for row in body["rows"])


def test_mailing_list_endpoint_is_a_flattened_preview_that_sends_nothing(board):
    Q.build_queue_entry("+491234567893", {"qualification_path": "urkunde", "city": "München", "department_pref": "Intensiv/IMC"})
    body = QA.wa_queue_mailing_list()
    assert body["total"] >= 1
    assert "candidates" in body and "clinics_with_contact" in body
    assert all(set(row.keys()) >= {"phone", "clinic_id", "score"} for row in body["rows"])
