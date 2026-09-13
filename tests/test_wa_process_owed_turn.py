"""TASK-76/77/79: the shared process_owed_turn pipeline in app/wa/api.py -- reply-turn claims
prevent double-answering the same inbound message, the per-candidate rate cap skips the brain
without losing the message, and a send failure is durably recorded. Fakes app.wa.luna_brain.turn
(no real CLI) and app.wa.meta.Client (no network), same patterns as
tests/test_wa_api_queue_trigger.py and tests/test_wa_harness.py.
"""
import time
from datetime import datetime, timezone

import pytest

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import store as ST

PHONE_ID = "111222333"
LEAD = "+491701234567"


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


class FailingMeta(FakeMeta):
    def send_text(self, to_e164, body):
        raise M.MetaError("Meta HTTP 500", status_code=500, payload={})


def payload(text, wamid="wamid.1", phone="491701234567"):
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
                "messages": [{"id": wamid, "from": phone, "type": "text", "text": {"body": text}}]}}]}]}


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    return FakeMeta()


def _fake_turn_result(bubbles=("ok",), action="reply_now_conversational"):
    return {"bubbles": list(bubbles), "buttons": [], "slots": {}, "asked": [],
            "stopped": False, "matches": [], "action": action}


# --- reply-turn claim: the same inbound message is never answered twice ------------------------

def test_a_turn_key_already_claimed_is_not_answered_again(wa, monkeypatch):
    calls = []
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None:
                        calls.append(1) or _fake_turn_result())

    with ST.db() as c:
        t = ST.thread(c, LEAD)
        assert ST.claim_reply_turn(c, LEAD, "wamid.1") is True   # something else already claimed it
        result = WAPI.process_owed_turn(c, t, "Hallo", None, "wamid.1", client=wa)

    assert result["status"] == "claimed_elsewhere"
    assert calls == [], "the brain must never be called once the turn_key is already claimed"


def test_two_calls_with_different_turn_keys_both_proceed(wa, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        r1 = WAPI.process_owed_turn(c, t, "Hallo", None, "wamid.1", client=wa)
        r2 = WAPI.process_owed_turn(c, t, "Und?", None, "wamid.2", client=wa)
    assert r1["status"] == "sent" and r2["status"] == "sent"


# --- per-candidate rate limit (TASK-76) ----------------------------------------------------------

def test_hitting_the_rate_cap_skips_the_brain_without_losing_the_message(wa, monkeypatch):
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 1)
    calls = []
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None:
                        calls.append(1) or _fake_turn_result())

    with ST.db() as c:
        t = ST.thread(c, LEAD)
        r1 = WAPI.process_owed_turn(c, t, "Hallo", None, "wamid.1", client=wa)
        r2 = WAPI.process_owed_turn(c, t, "Zweite Nachricht", None, "wamid.2", client=wa)

    assert r1["status"] == "sent"
    assert r2["status"] == "rate_limited"
    assert len(calls) == 1, "the brain must not be called once the cap is hit"


def test_rate_limit_of_zero_disables_the_cap(wa, monkeypatch):
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        for i in range(5):
            r = WAPI.process_owed_turn(c, t, f"msg {i}", None, f"wamid.{i}", client=wa)
            assert r["status"] == "sent"


def test_a_rate_limited_turn_is_reclaimable_by_a_later_catch_up_pass(wa, monkeypatch):
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    with ST.db() as c:
        t = ST.thread(c, LEAD)
        assert ST.claim_reply_turn(c, LEAD, "wamid.1") is True
        ST.finish_reply_turn_claim(c, LEAD, "wamid.1", "skipped_rate_cap")
        monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
        result = WAPI.process_owed_turn(c, t, "Hallo", None, "wamid.1", client=wa)
    assert result["status"] == "sent"


# --- send-failure visibility (TASK-79) ------------------------------------------------------------

def test_a_send_failure_is_durably_recorded_before_reraising(wa, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    with pytest.raises(M.MetaError):
        WAPI.handle_payload(payload("Hallo"), client=FailingMeta())
    with ST.db() as c:
        failure = ST.recent_send_failure(c, LEAD)
    assert failure is not None
    assert "500" in failure["error"] or "Meta" in failure["error"]


def test_a_failed_send_leaves_the_claim_reclaimable(wa, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    with pytest.raises(M.MetaError):
        WAPI.handle_payload(payload("Hallo", wamid="wamid.f"), client=FailingMeta())
    with ST.db() as c:
        assert ST.claim_reply_turn(c, LEAD, "wamid.f") is True, (
            "a failed send must not permanently block a retry of the same inbound message")


def test_no_failure_is_recorded_on_a_healthy_send(wa, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result())
    WAPI.handle_payload(payload("Hallo"), client=wa)
    with ST.db() as c:
        assert ST.recent_send_failure(c, LEAD) is None


# --- stuck-reply flag (TASK-79) -------------------------------------------------------------------

def test_is_stuck_false_for_a_thread_that_just_wrote(wa):
    with ST.db() as c:
        ST.record_inbound(c, LEAD, "wamid.1", "Hallo")
        t = ST.thread(c, LEAD)
        t["last_inbound_at"] = ST.now_iso()
        ST.save_thread(c, t)
        assert WAPI._is_stuck(c, LEAD, t["last_inbound_at"], False) is False


def test_is_stuck_true_once_past_the_configured_hours(wa, monkeypatch):
    monkeypatch.setattr(C, "STUCK_REPLY_HOURS", 2.0)
    with ST.db() as c:
        ST.record_inbound(c, LEAD, "wamid.1", "Hallo")
        old = "2020-01-01T00:00:00+00:00"
        assert WAPI._is_stuck(c, LEAD, old, False) is True


def test_is_stuck_false_once_we_have_already_answered(wa, monkeypatch):
    monkeypatch.setattr(C, "STUCK_REPLY_HOURS", 2.0)
    with ST.db() as c:
        ST.record_inbound(c, LEAD, "wamid.1", "Hallo")
        ST.record_outbound(c, LEAD, "wamid.out.1", "Willkommen!")
        old = "2020-01-01T00:00:00+00:00"
        assert WAPI._is_stuck(c, LEAD, old, False) is False


def test_is_stuck_false_for_a_stopped_thread(wa):
    old = "2020-01-01T00:00:00+00:00"
    with ST.db() as c:
        ST.record_inbound(c, LEAD, "wamid.1", "STOP")
        assert WAPI._is_stuck(c, LEAD, old, True) is False
