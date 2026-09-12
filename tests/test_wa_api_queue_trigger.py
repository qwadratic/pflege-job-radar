"""TASK-66: the queue-build trigger in app/wa/api.py -- fires exactly when anonymous_send_consent
newly flips true on a WA_BRAIN=luna turn, after the thread is durably saved, and never for the
deterministic brain or an already-consented thread. Fakes app.wa.luna_brain.turn (no real CLI)."""
import time

import pytest

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import queue as Q
from app.wa import store as ST

PHONE_ID = "111222333"


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


def _fake_turn_result(consent, action="reply_now_conversational"):
    return {"bubbles": ["ok"], "buttons": [], "slots": {"anonymous_send_consent": consent}, "asked": [],
            "stopped": False, "matches": [], "action": action}


def test_a_fresh_consent_triggers_a_queue_build_after_the_turn_is_answered(wa, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result(True))
    calls = []
    monkeypatch.setattr(Q, "build_queue_entry", lambda phone, card, cv_profile=None: calls.append((phone, card)))

    out = WAPI.handle_payload(payload("Ja, ich bin einverstanden"), client=wa)

    assert out["results"][0]["status"] == "sent", "the turn must still be answered normally"
    assert len(calls) == 1
    phone, card = calls[0]
    assert phone == "+491701234567"
    assert card.get("anonymous_send_consent") is True

    conn = ST.db()
    try:
        t = ST.thread(conn, "+491701234567")
        assert t["slots"].get("anonymous_send_consent") is True, "the consent must be durably saved"
    finally:
        conn.close()


def test_an_already_consented_thread_does_not_trigger_again(wa, monkeypatch):
    conn = ST.db()
    t = ST.thread(conn, "+491701234567")
    t["slots"] = {"anonymous_send_consent": True}
    ST.save_thread(conn, t)
    conn.close()

    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result(True))
    calls = []
    monkeypatch.setattr(Q, "build_queue_entry", lambda phone, card, cv_profile=None: calls.append((phone, card)))

    WAPI.handle_payload(payload("Danke nochmal"), client=wa)
    assert calls == [], "consent was already true before this turn -- must not rebuild the queue"


def test_no_consent_this_turn_does_not_trigger(wa, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result(None))
    calls = []
    monkeypatch.setattr(Q, "build_queue_entry", lambda phone, card, cv_profile=None: calls.append((phone, card)))

    WAPI.handle_payload(payload("Hallo"), client=wa)
    assert calls == []


def test_deterministic_brain_never_triggers_the_queue_build(wa, monkeypatch):
    monkeypatch.setattr(C, "BRAIN", "deterministic")
    calls = []
    monkeypatch.setattr(Q, "build_queue_entry", lambda phone, card, cv_profile=None: calls.append((phone, card)))
    WAPI.handle_payload(payload("Hallo, ich suche einen Pflegejob"), client=wa)
    assert calls == [], "the deterministic brain has no anonymous_send_consent concept at all"


def test_internal_trigger_keys_never_leak_into_the_api_response(wa, monkeypatch):
    monkeypatch.setattr(LB, "turn", lambda text, thread, button_id=None, client=None: _fake_turn_result(True))
    monkeypatch.setattr(Q, "build_queue_entry", lambda phone, card, cv_profile=None: None)
    out = WAPI.handle_payload(payload("Ja"), client=wa)
    assert "_newly_consented_phone" not in out["results"][0]
    assert "_card_at_consent" not in out["results"][0]
