"""Offline tests for app/wa/router.py (TASK-84) -- no real Meta traffic, no network. The forward
transport is a fake (same swappable-transport seam as app/wa/meta.py), and 'us' messages go
through the real app.wa.api.handle_payload() against a temp sqlite file, same as tests/test_wa_harness.py.
"""
import hashlib
import hmac
import json
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import router as ROUTER
from app.wa import routing as R
from app.wa import store as ST

APP_SECRET = "test-app-secret"
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


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "APP_SECRET", APP_SECRET)
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "AUTOSEND", True)
    return FakeMeta()


def _msg(text, wamid, phone):
    return {"id": wamid, "from": phone, "type": "text", "text": {"body": text}}


def _payload(messages):
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
                "messages": messages}}]}]}


def _sign(body):
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _mark_owner(phone, owner):
    with R.db() as c:
        c.execute("insert into wa_ownership (phone, owner, reason, since) values (?,?,?,?) "
                  "on conflict(phone) do update set owner=excluded.owner",
                  (phone, owner, "test", "2026-01-01T00:00:00+00:00"))
        c.commit()


# --- signature trust boundary --------------------------------------------------------------------

def test_a_bad_signature_raises_permission_error(wa):
    body = json.dumps(_payload([_msg("hi", "w1", "491701234567")])).encode()
    with pytest.raises(PermissionError):
        ROUTER.route_webhook(body, "sha256=deadbeef")


def test_a_missing_signature_raises_permission_error(wa):
    body = json.dumps(_payload([_msg("hi", "w1", "491701234567")])).encode()
    with pytest.raises(PermissionError):
        ROUTER.route_webhook(body, None)


# --- split by owner --------------------------------------------------------------------------

def test_an_all_us_payload_is_processed_locally_and_nothing_is_forwarded(wa):
    _mark_owner("+491701234567", "us")
    body = json.dumps(_payload([_msg("Hallo", "w1", "491701234567")])).encode()
    forwarded = []
    result = ROUTER.route_webhook(body, _sign(body), meta_client=wa, forward=lambda b, h: forwarded.append(b))
    assert result["them_forwarded"] is False
    assert result["us"]["handled"] == 1
    assert forwarded == []
    with ST.db() as c:
        assert ST.history(c, "+491701234567"), "the us-owned message must have actually been processed"


def test_an_all_them_payload_is_forwarded_and_not_processed_locally(wa, monkeypatch):
    _mark_owner("+491701234567", "them")
    monkeypatch.setattr(C, "REAL_SYSTEM_WEBHOOK_URL", "https://real-system.example/webhook")
    body = json.dumps(_payload([_msg("Hallo", "w1", "491701234567")])).encode()
    forwarded = []
    result = ROUTER.route_webhook(body, _sign(body), meta_client=wa,
                                  forward=lambda b, h: forwarded.append((b, h)))
    assert result["them_forwarded"] is True
    assert result["us"] is None
    assert len(forwarded) == 1
    with ST.db() as c:
        assert ST.history(c, "+491701234567") == [], "a them-owned message must never be processed locally"


def test_a_mixed_payload_splits_correctly(wa, monkeypatch):
    _mark_owner("+491111111111", "us")
    _mark_owner("+492222222222", "them")
    monkeypatch.setattr(C, "REAL_SYSTEM_WEBHOOK_URL", "https://real-system.example/webhook")
    body = json.dumps(_payload([_msg("us msg", "w1", "491111111111"),
                                _msg("them msg", "w2", "492222222222")])).encode()
    forwarded = []
    result = ROUTER.route_webhook(body, _sign(body), meta_client=wa,
                                  forward=lambda b, h: forwarded.append(json.loads(b)))
    assert result["them_forwarded"] is True
    assert result["us"]["handled"] == 1
    with ST.db() as c:
        assert ST.history(c, "+491111111111"), "the us phone must be processed"
        assert ST.history(c, "+492222222222") == [], "the them phone must not be processed locally"
    them_messages = forwarded[0]["entry"][0]["changes"][0]["value"]["messages"]
    assert [m["id"] for m in them_messages] == ["w2"], "only the them message forwards, not the us one"


def test_a_new_unrouted_phone_defaults_to_us_when_unknown_to_the_real_system(wa, tmp_path, monkeypatch):
    known_file = tmp_path / "known.txt"
    known_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known_file))
    body = json.dumps(_payload([_msg("Hallo", "w1", "491701234567")])).encode()
    result = ROUTER.route_webhook(body, _sign(body), meta_client=wa)
    assert result["them_forwarded"] is False
    assert result["us"]["handled"] == 1


# --- forwarding failure modes ------------------------------------------------------------------

def test_forwarding_without_a_configured_url_raises_loudly_not_silently(wa):
    _mark_owner("+491701234567", "them")
    assert not C.REAL_SYSTEM_WEBHOOK_URL
    body = json.dumps(_payload([_msg("Hallo", "w1", "491701234567")])).encode()
    with pytest.raises(RuntimeError, match="WA_REAL_SYSTEM_WEBHOOK_URL"):
        ROUTER.route_webhook(body, _sign(body), meta_client=wa)


def test_the_forwarded_signature_is_valid_over_the_reconstructed_body(wa, monkeypatch):
    _mark_owner("+491701234567", "them")
    monkeypatch.setattr(C, "REAL_SYSTEM_WEBHOOK_URL", "https://real-system.example/webhook")
    body = json.dumps(_payload([_msg("Hallo", "w1", "491701234567")])).encode()
    captured = {}
    ROUTER.route_webhook(body, _sign(body), meta_client=wa,
                         forward=lambda b, h: captured.update(body=b, headers=h))
    expected = _sign(captured["body"])
    assert captured["headers"]["X-Hub-Signature-256"] == expected


def test_a_payload_with_no_messages_at_all_does_nothing(wa):
    body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    result = ROUTER.route_webhook(body, _sign(body), meta_client=wa)
    assert result == {"us": None, "them_forwarded": False}
