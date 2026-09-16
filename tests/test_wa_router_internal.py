"""Offline tests for app/wa/router.py's local-only internal receiver (TASK-86). Uses Starlette
TestClient's client= override to simulate a real loopback vs. non-local origin end to end."""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import data as D
from app.wa import config as C
from app.wa import store as ST
from app.wa.router import router as wa_router

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
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "AUTOSEND", True)
    return FakeMeta()


def _payload():
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
                "messages": [{"id": "w1", "from": "491701234567", "type": "text",
                             "text": {"body": "Hallo"}}]}}]}]}


def _local_app():
    app = FastAPI()
    app.include_router(wa_router, prefix="/api")
    return TestClient(app, client=("127.0.0.1", 12345))


def _remote_app():
    app = FastAPI()
    app.include_router(wa_router, prefix="/api")
    return TestClient(app)  # default client=("testclient", 50000) -- not loopback


def test_local_and_enabled_succeeds_and_processes_the_payload(wa, monkeypatch):
    monkeypatch.setattr(C, "INTERNAL_WEBHOOK_ENABLED", True)
    import app.wa.api as api_mod
    monkeypatch.setattr(api_mod.M, "Client", lambda *a, **k: wa)
    with _local_app() as client:
        resp = client.post("/api/wa/internal-webhook", json=_payload())
    assert resp.status_code == 200
    assert resp.json()["handled"] == 1
    with ST.db() as c:
        assert ST.history(c, "+491701234567"), "the payload must have actually reached handle_payload"


def test_local_but_disabled_is_rejected(wa, monkeypatch):
    monkeypatch.setattr(C, "INTERNAL_WEBHOOK_ENABLED", False)
    with _local_app() as client:
        resp = client.post("/api/wa/internal-webhook", json=_payload())
    assert resp.status_code == 403


def test_non_local_even_when_enabled_is_rejected(wa, monkeypatch):
    monkeypatch.setattr(C, "INTERNAL_WEBHOOK_ENABLED", True)
    with _remote_app() as client:
        resp = client.post("/api/wa/internal-webhook", json=_payload())
    assert resp.status_code == 403
    with ST.db() as c:
        assert ST.history(c, "+491701234567") == [], "a rejected call must never reach handle_payload"


def test_disabled_and_non_local_is_also_rejected(wa, monkeypatch):
    monkeypatch.setattr(C, "INTERNAL_WEBHOOK_ENABLED", False)
    with _remote_app() as client:
        resp = client.post("/api/wa/internal-webhook", json=_payload())
    assert resp.status_code == 403


def test_default_config_is_disabled(wa):
    assert C.INTERNAL_WEBHOOK_ENABLED is False
