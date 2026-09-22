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
    """Board rows complete enough for both readers of this snapshot: app.autopilot.matching (via
    build_queue_entry) and app/wa/luna_brain.py:market_snapshot, which is what lets the housing agreement
    test below compare the shortlist Luna names with the queue the human gets. Only c1 offers housing."""
    return [{"posting_id": 1, "clinic_id": "c1", "role_class": "pflegefachkraft",
             "department_hint": "Intensiv/IMC", "qualification_hint": None, "title": "Pflegefachkraft Intensiv",
             "clinic_name": "Klinikum München", "employer": "Klinikum München", "city": "München",
             "clinic_town": "München", "regierungsbezirk": "Oberbayern", "employment_types": ["vollzeit"],
             "enr_housing": True, "status": "open", "verify_status": "live", "first_published": "2026-09-01"},
            {"posting_id": 2, "clinic_id": "c2", "role_class": "pflegefachkraft",
             "department_hint": "Innere Medizin", "qualification_hint": None, "title": "Pflegefachkraft Innere",
             "clinic_name": "Klinikum Augsburg", "employer": "Klinikum Augsburg", "city": "Augsburg",
             "clinic_town": "Augsburg", "regierungsbezirk": "Schwaben", "employment_types": ["vollzeit"],
             "enr_housing": False, "status": "open", "verify_status": "live", "first_published": "2026-09-02"}]


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


# --- TASK-108: the handoff uses the same housing criterion as the shortlist Luna named -------------

_READY_CARD = {"qualification_path": "urkunde", "qualification_ok": True, "department_pref": "egal",
               "documents": [{"id": 1, "document_type": "lebenslauf", "certificate_level": "unknown"},
                             {"id": 2, "document_type": "urkunde", "certificate_level": "fachkraft"}]}


def test_card_to_candidate_carries_the_housing_answer(board):
    cand = Q.card_to_candidate({"qualification_path": "urkunde", "housing_needed": True, "people_count": 3})
    assert cand["needs_housing"] is True and cand["people_count"] == 3
    assert Q.card_to_candidate({"housing_needed": False})["needs_housing"] is False
    assert Q.card_to_candidate({})["needs_housing"] is None, "an unanswered gate is not a no"


def test_a_candidate_who_needs_a_flat_is_ranked_only_against_clinics_the_board_marks(board):
    out = Q.build_queue_entry("+491234500001", {**_READY_CARD, "housing_needed": True, "people_count": 2})
    assert [m["clinic_id"] for m in out["matches"]] == ["c1"], (
        "c2 has an open posting but no housing mark -- the human handoff must not get it for a candidate "
        "who was told the search is restricted to clinics with a flat")


def test_without_a_housing_need_every_clinic_still_ranks(board):
    out = Q.build_queue_entry("+491234500002", {**_READY_CARD, "housing_needed": False})
    assert sorted(m["clinic_id"] for m in out["matches"]) == ["c1", "c2"]


def test_the_shortlist_luna_names_and_the_queue_the_human_gets_agree_on_housing(board):
    """The promise and the handoff come from one criterion (app.data.offers_housing): every clinic in the
    close-sequence shortlist is in the queue, and the queue holds nothing the shortlist ruled out."""
    from app.wa import luna_brain as LB

    card = {**_READY_CARD, "housing_needed": True, "people_count": 2}
    shortlist = LB.market_snapshot(card)["shortlist"]
    assert [(s["clinic"], s["housing"]) for s in shortlist] == [("Klinikum München", True)]
    out = Q.build_queue_entry("+491234500003", card)
    assert {s["clinic"] for s in shortlist} == {m["name"] for m in out["matches"]}


def test_a_candidate_who_accepts_a_clinic_without_a_flat_is_ranked_wider_and_still_reads_as_needing_one(board):
    """Review 2026-09-16: "wanted a flat, would also take a clinic without one" had no field of its own, so
    the only way to record it was flipping housing_needed to false -- and the human working the queue then
    read "needs no flat" for a family of two who asked for one. housing_flexible widens the ranking (the same
    rule the shortlist uses) while needs_housing/people_count keep saying what was asked for."""
    card = {**_READY_CARD, "housing_needed": True, "people_count": 2, "housing_flexible": True}
    cand = Q.card_to_candidate(card)
    assert (cand["needs_housing"], cand["housing_flexible"], cand["people_count"]) == (True, True, 2)

    out = Q.build_queue_entry("+491234500004", card)
    assert sorted(m["clinic_id"] for m in out["matches"]) == ["c1", "c2"]
    from app.wa import luna_brain as LB
    assert {s["clinic"] for s in LB.market_snapshot(card)["shortlist"]} == {m["name"] for m in out["matches"]}
    assert out["candidate"]["needs_housing"] is True, "the stored profile the human reads keeps the need"


def test_an_imported_card_with_only_the_housing_flag_is_not_matched_as_if_it_answered(board):
    """housing_known alone (the import shape) is not an answer: needs_housing stays null, and the gate that
    would have to be settled before a queue entry exists is still open (tests/test_wa_luna_brain.py)."""
    cand = Q.card_to_candidate({**_READY_CARD, "housing_known": True})
    assert cand["needs_housing"] is None and cand["housing_flexible"] is None


# --- TASK-144: the branch the candidate chose decides how many clinics are queued -----------------
# "narrow"/"pool" are written on the card verbatim by app/wa/luna_brain.py (the values of
# app/wa/luna/offer.py:BRANCH_NARROW/BRANCH_POOL), so they are spelled out here as the cards carry them.

def _pool_jobs(n):
    """n open postings in n distinct München clinics, all matching a qualified Pflegefachkraft."""
    return [{"posting_id": i, "clinic_id": f"p{i}", "role_class": "pflegefachkraft",
             "department_hint": "Intensiv/IMC", "qualification_hint": None, "title": "Pflegefachkraft Intensiv",
             "clinic_name": f"Klinikum München {i}", "employer": f"Klinikum München {i}", "city": "München",
             "clinic_town": "München", "regierungsbezirk": "Oberbayern", "employment_types": ["vollzeit"],
             "enr_housing": False, "status": "open", "verify_status": "live", "first_published": "2026-09-01",
             "fresh": True}
            for i in range(1, n + 1)]


def _pool_clinics(n):
    return [{"clinic_id": f"p{i}", "name": f"Klinikum München {i}", "town": "München",
             "regierungsbezirk": "Oberbayern", "beds": 500, "jobs_open": 1, "fachrichtungen": []}
            for i in range(1, n + 1)]


@pytest.fixture()
def twelve_matching_clinics(tmp_path, monkeypatch):
    """12 matching clinics -- comfortably more than the five one message may show, so a queue that
    still stops at five is visible as five, not as "the board happened to be small"."""
    jobs, clinics = _pool_jobs(12), _pool_clinics(12)
    D._snap.update({"at": time.time(), "jobs": jobs, "clinics": clinics,
                    "by_clinic": {c["clinic_id"]: c for c in clinics}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")


_POOL_CARD = {**_READY_CARD, "housing_needed": False, "city": "München"}


def test_the_pool_branch_queues_every_matching_clinic_not_five(twelve_matching_clinics):
    """Ivan, 2026-09-21: five caps one message; "to all of them" means all X clinics that matched.
    The audit case was 224 matching clinics and 5 queued rows."""
    out = Q.build_queue_entry("+491234500010", {**_POOL_CARD, "match_branch": "pool"})
    assert len(out["matches"]) == 12

    conn = Q.db()
    try:
        rows = conn.execute("select clinic_id from wa_queue_matches where phone=?",
                            ("+491234500010",)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 12
    assert {r["clinic_id"] for r in rows} == {f"p{i}" for i in range(1, 13)}


def test_the_narrow_branch_keeps_queueing_five(twelve_matching_clinics):
    out = Q.build_queue_entry("+491234500011", {**_POOL_CARD, "match_branch": "narrow"})
    assert len(out["matches"]) == 5


def test_a_card_that_never_chose_a_branch_queues_five_as_before(twelve_matching_clinics):
    """Threads from before match_branch existed, and consents that never went through the offer
    turn: unchanged behaviour, not a silent promotion to the pool."""
    out = Q.build_queue_entry("+491234500012", _POOL_CARD)
    assert len(out["matches"]) == 5


def test_an_unrecognised_branch_value_raises_instead_of_picking_one(twelve_matching_clinics):
    with pytest.raises(ValueError, match="match_branch"):
        Q.build_queue_entry("+491234500013", {**_POOL_CARD, "match_branch": "alle"})
    conn = Q.db()
    try:
        rows = conn.execute("select phone from wa_queue_candidates where phone=?",
                            ("+491234500013",)).fetchall()
    finally:
        conn.close()
    assert rows == [], "a card nobody can read must queue nothing at all, not a default five"


def test_the_message_says_how_many_matched_and_the_pool_queue_has_that_many(twelve_matching_clinics):
    """The promise and the action, from one board: the offer shows 5 positions and says 12 clinics
    matched; the pool consent that follows queues 12."""
    from app.wa import luna_brain as LB

    card = {**_POOL_CARD, "match_branch": "pool"}
    offer = LB.market_snapshot(card)["offer"]
    assert (offer["shown"], offer["clinics_total"]) == (5, 12)
    out = Q.build_queue_entry("+491234500014", card)
    assert len(out["matches"]) == 12


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
