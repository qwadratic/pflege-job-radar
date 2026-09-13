"""Offline tests for app/wa/luna/shadow_run.py -- fixture threads only, no real subprocess, no
real database ever mutated."""
import sqlite3
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from app.wa.luna import shadow_run as SR


@pytest.fixture()
def db(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    conn = ST.db()
    yield conn
    conn.close()


def fake_client(out_or_fn):
    """Same pattern as tests/test_wa_luna_brain.py: a Client whose reply() either returns a fixed
    dict, or delegates to a function of (system, user, session_id) -> (dict, session_id)."""
    if callable(out_or_fn):
        fn = out_or_fn
    else:
        fn = lambda system, user, session_id: (out_or_fn, session_id)
    return LB.Client(reply=fn)


def _out(**kw):
    base = {"action": "reply_now_conversational", "bubbles": ["Hallo 🙂"], "rationale": "",
            "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


def _seed_thread(conn, phone, slots=None, last_inbound_at=None):
    t = ST.thread(conn, phone)
    t["slots"] = slots or {}
    if last_inbound_at is not None:
        t["last_inbound_at"] = last_inbound_at
    ST.save_thread(conn, t)
    return t


# --- phones_owed_a_reply -----------------------------------------------------------------------

def test_phones_owed_a_reply_only_lists_threads_whose_last_message_is_inbound(db):
    _seed_thread(db, "+49111")
    ST.record_inbound(db, "+49111", "wamid.1", "Hallo")

    _seed_thread(db, "+49222")
    ST.record_inbound(db, "+49222", "wamid.2", "Hallo")
    ST.record_outbound(db, "+49222", "wamid.3", "Willkommen!")

    assert SR.phones_owed_a_reply(db) == ["+49111"]


def test_phones_owed_a_reply_is_empty_with_no_messages_at_all(db):
    _seed_thread(db, "+49111")
    assert SR.phones_owed_a_reply(db) == []


# --- shadow_turn (deterministic brain, the default) --------------------------------------------

def test_shadow_turn_none_for_a_phone_with_no_thread(db):
    assert SR.shadow_turn(db, "+49999") is None


def test_shadow_turn_reports_a_stopped_thread_without_calling_any_brain(db):
    t = _seed_thread(db, "+49111")
    t["stopped"], t["stopped_reason"] = True, ST.STOPPED
    ST.save_thread(db, t)
    ST.record_inbound(db, "+49111", "wamid.1", "STOP")

    row = SR.shadow_turn(db, "+49111")
    assert row["gate"] == "stopped"
    assert row["bubbles"] == []
    assert row["would_stop"] is True


def test_shadow_turn_deterministic_brain_reflects_the_real_turn_decision(db):
    _seed_thread(db, "+49111")
    ST.record_inbound(db, "+49111", "wamid.1", "Ich suche eine Stelle als Pflegefachkraft")

    row = SR.shadow_turn(db, "+49111")
    assert row["phone"] == "+49111"
    assert row["gate"] == "freeform"
    assert row["window_open"] is True
    assert row["bubbles"], "a fresh lead asking about a role should get a real reply, not silence"


# --- shadow_turn (luna brain) --------------------------------------------------------------------

def test_shadow_turn_luna_brain_never_resumes_the_live_session(db, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    _seed_thread(db, "+49111", slots={"_session_id": "real-live-session-do-not-touch"})
    ST.record_inbound(db, "+49111", "wamid.1", "Hallo")

    seen_session_ids = []

    def fn(system, user, session_id):
        seen_session_ids.append(session_id)
        return _out(), session_id

    row = SR.shadow_turn(db, "+49111", client=fake_client(fn))
    assert seen_session_ids == [None], "shadow_turn must strip _session_id before calling the brain"
    assert row["bubbles"] == ["Hallo 🙂"]


def test_shadow_turn_luna_brain_reports_the_gate_and_stage(db, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    _seed_thread(db, "+49111", slots={"qualification_path": "urkunde"})
    ST.record_inbound(db, "+49111", "wamid.1", "Hallo")

    row = SR.shadow_turn(db, "+49111", client=fake_client(_out(bubbles=["Erzähl mir mehr."])))
    assert row["stage"] == "qualifying"
    assert row["gate"] == "freeform"
    assert row["action"] == "reply_now_conversational"


def test_shadow_turn_reports_no_send_when_the_model_has_nothing_new_to_say(db, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "luna")
    _seed_thread(db, "+49111")
    ST.record_inbound(db, "+49111", "wamid.1", "ok danke")

    row = SR.shadow_turn(db, "+49111", client=fake_client(_out(no_send=True, bubbles=[])))
    assert row["gate"] == "no_send"
    assert row["bubbles"] == []


# --- shadow_turn (TASK-70 24h window gate) ------------------------------------------------------

def test_shadow_turn_reports_reopen_template_missing_when_window_closed(db, monkeypatch):
    monkeypatch.setattr(C, "FREEFORM_WINDOW_HOURS", 24.0)
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "")
    old = "2020-01-01T00:00:00+00:00"
    _seed_thread(db, "+49111", last_inbound_at=old)
    ST.record_inbound(db, "+49111", "wamid.1", "Hallo nochmal")

    row = SR.shadow_turn(db, "+49111")
    assert row["window_open"] is False
    assert row["gate"] == "reopen_template_missing"


def test_shadow_turn_reports_reopen_template_when_one_is_configured(db, monkeypatch):
    monkeypatch.setattr(C, "FREEFORM_WINDOW_HOURS", 24.0)
    monkeypatch.setattr(C, "WA_REOPEN_TEMPLATE_NAME", "wa_reopen_v1")
    old = "2020-01-01T00:00:00+00:00"
    _seed_thread(db, "+49111", last_inbound_at=old)
    ST.record_inbound(db, "+49111", "wamid.1", "Hallo nochmal")

    row = SR.shadow_turn(db, "+49111")
    assert row["gate"] == "reopen_template"


# --- db_copy / run: the never-touch-the-real-database contract ---------------------------------

def test_db_copy_is_read_only_and_never_creates_a_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        with SR.db_copy(missing):
            pass
    assert not missing.exists()


def test_run_leaves_the_real_database_completely_unchanged(db, tmp_path):
    _seed_thread(db, "+49111")
    ST.record_inbound(db, "+49111", "wamid.1", "Ich suche eine Stelle")
    db.close()  # so run() (a fresh connection) sees exactly what was just committed, nothing held open

    before = sqlite3.connect(C.SQLITE_PATH).execute("select count(*) from wa_messages").fetchone()[0]
    rows = SR.run()
    after_conn = sqlite3.connect(C.SQLITE_PATH)
    after = after_conn.execute("select count(*) from wa_messages").fetchone()[0]
    thread_row = after_conn.execute("select slots from wa_threads where phone=?", ("+49111",)).fetchone()

    assert len(rows) == 1 and rows[0]["phone"] == "+49111"
    assert after == before, "a dry run must never write an outbound message to the real database"
    assert "_session_id" not in thread_row[0], "the real thread's own card must be untouched"


def test_run_can_be_scoped_to_specific_phones(db):
    _seed_thread(db, "+49111")
    ST.record_inbound(db, "+49111", "wamid.1", "Hallo")
    _seed_thread(db, "+49222")
    ST.record_inbound(db, "+49222", "wamid.2", "Hallo")

    rows = SR.run(phones=["+49222"])
    assert [r["phone"] for r in rows] == ["+49222"]
