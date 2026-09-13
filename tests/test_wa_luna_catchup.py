"""Offline tests for app/wa/luna/catchup.py (TASK-78) -- fixture threads only, no real subprocess.
Fakes app.wa.luna_brain.turn and app.wa.meta.Client, same patterns as
tests/test_wa_process_owed_turn.py."""
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from app.wa.luna import catchup as CU


class FakeMeta:
    def __init__(self):
        self.sent = []
        self.n = 0

    def send_text(self, to_e164, body):
        self.n += 1
        self.sent.append({"to": to_e164, "body": body})
        return f"wamid.out.{self.n}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    conn = ST.db()
    yield conn
    conn.close()


def _fake_turn_result(bubbles=("Hallo zurück!",)):
    return {"bubbles": list(bubbles), "buttons": [], "slots": {}, "asked": [],
            "stopped": False, "matches": [], "action": "reply_now_conversational"}


def _seed_owed(conn, phone, wamid=None, text="Hallo"):
    t = ST.thread(conn, phone)
    ST.save_thread(conn, t)
    ST.record_inbound(conn, phone, wamid or f"wamid.{phone}", text)


def test_run_answers_every_owed_thread(db, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    _seed_owed(db, "+49111")
    _seed_owed(db, "+49222")
    db.close()

    results = CU.run(client=FakeMeta())
    assert {r["phone"] for r in results} == {"+49111", "+49222"}
    assert all(r["status"] == "sent" for r in results)


def test_run_skips_a_thread_that_already_has_a_reply(db, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    _seed_owed(db, "+49111")
    ST.record_outbound(db, "+49111", "wamid.out.0", "already answered")
    db.close()

    results = CU.run(client=FakeMeta())
    assert results == []


def test_run_skips_a_stopped_thread(db, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    t = ST.thread(db, "+49111")
    t["stopped"], t["stopped_reason"] = True, ST.STOPPED
    ST.save_thread(db, t)
    ST.record_inbound(db, "+49111", "wamid.1", "STOP")
    db.close()

    results = CU.run(client=FakeMeta())
    assert results == []


def test_a_thread_already_claimed_by_a_concurrent_webhook_is_skipped(db, monkeypatch):
    calls = []
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None:
                        calls.append(1) or _fake_turn_result())
    _seed_owed(db, "+49111", wamid="wamid.1")
    assert ST.claim_reply_turn(db, "+49111", "wamid.1") is True  # a "concurrent webhook" got there first
    db.close()

    results = CU.run(client=FakeMeta())
    assert results == [{"phone": "+49111", "status": "claimed_elsewhere"}]
    assert calls == [], "the brain must not be called once the turn is already claimed"


def test_a_rate_capped_thread_stays_owed_for_the_next_run(db, monkeypatch):
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 1)
    calls = []
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None:
                        calls.append(1) or _fake_turn_result())
    _seed_owed(db, "+49111")
    ST.record_luna_call(db, "+49111")  # a call already made this hour -- the next one is over cap
    db.close()

    results = CU.run(client=FakeMeta())
    assert results == [{"phone": "+49111", "status": "rate_limited"}]
    assert calls == []


def test_run_can_be_scoped_to_specific_phones(db, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    _seed_owed(db, "+49111")
    _seed_owed(db, "+49222")
    db.close()

    results = CU.run(client=FakeMeta(), phones=["+49222"])
    assert [r["phone"] for r in results] == ["+49222"]


def test_run_never_touches_a_phone_with_no_thread_at_all(db):
    results = CU.run(client=FakeMeta(), phones=["+49999"])
    assert results == []
