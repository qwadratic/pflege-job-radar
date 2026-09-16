"""TASK-99: the webhook records in the request and finishes turns in one background worker; catch-up finishes
whatever the worker did not (a failure, a crash or restart mid-turn), media included. Synthetic phones, a tmp
SQLite, fake Meta clients and a fake brain -- nothing reaches Meta or the claude CLI.
"""
import hashlib
import hmac
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import cv as CV
from app import data as D
from app.wa import api as WAPI
from app.wa import asgi
from app.wa import brain as B
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import meta as M
from app.wa import router as ROUTER
from app.wa import routing as R
from app.wa import store as ST
from app.wa.luna import catchup as CU
from tests.test_wa_media_intake import FakeMetaMedia, _classify_by_text, _meta_with, _PayloadLog

APP_SECRET = "test-app-secret"
PHONE_ID = "111222333"
LEAD, OTHER = "491701234567", "492229998877"
GATE_SEC = 20


FakeMeta = FakeMetaMedia   # records sends, serves only the media ids it is given


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "DOCUMENTS_DIR", tmp_path / "wa_documents")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "APP_SECRET", APP_SECRET)
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "REAL_SYSTEM_WEBHOOK_URL", "https://real-system.example/webhook")
    meta = FakeMeta()
    monkeypatch.setattr(M, "Client", lambda *a, **k: meta)
    return meta


class Brain:
    """Stands in for app/wa/brain.py:turn. ``gate`` (when set) holds a turn until released."""

    def __init__(self, monkeypatch, fail_on=()):
        self.texts, self.started, self.gate, self.fail_on = [], threading.Event(), None, set(fail_on)
        monkeypatch.setattr(B, "turn", self.turn)

    def turn(self, text, thread, button_id=None):
        self.texts.append(text)
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(GATE_SEC), "test never released the slow turn"
        if text in self.fail_on:
            raise RuntimeError(f"brain failed on {text!r}")
        return {"bubbles": [f"re: {text}"], "buttons": [], "slots": thread["slots"], "asked": thread["asked"],
                "stopped": False, "matches": [], "action": "reply"}


def _text(text, wamid, phone=LEAD):
    return {"id": wamid, "from": phone, "type": "text", "text": {"body": text}}


def _payload(*messages, **value):
    return {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
        **({"messages": list(messages)} if messages else {}), **value}}]}]}


def _headers(raw):
    return {"X-Hub-Signature-256": "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest(),
            "Content-Type": "application/json"}


def _post(client, path, body):
    raw = json.dumps(body).encode()
    return client.post(path, content=raw, headers=_headers(raw))


def _owner(phone, owner):
    with R.db() as c:
        R._set_ownership(c, "+" + phone, owner, "test")


def _pending():
    with ST.db() as c:
        return [r[0] for r in c.execute("select wamid from wa_inbound_pending order by recorded_at, wamid")]


def _history(phone=LEAD):
    with ST.db() as c:
        return [(m["direction"], m["body"]) for m in ST.history(c, "+" + phone)]


# --- the request records, the worker answers --------------------------------------------------------------

def test_the_webhook_answers_200_with_the_message_recorded_before_the_turn_runs(wa, monkeypatch):
    brain = Brain(monkeypatch)
    brain.gate = threading.Event()
    with TestClient(asgi.app) as client:
        r = _post(client, "/api/wa/webhook", _payload(_text("Hallo", "w1")))
        assert r.status_code == 200 and r.json()["results"] == [{"wamid": "w1", "status": "accepted"}]
        assert brain.started.wait(GATE_SEC)
        assert _history() == [("in", "Hallo")] and _pending() == ["w1"], "recorded and still pending mid-turn"
        brain.gate.set()
        WAPI.wait_for_background(GATE_SEC)
    assert _history() == [("in", "Hallo"), ("out", "re: Hallo")] and _pending() == []
    with ST.db() as c:
        t = ST.thread(c, "+" + LEAD)
    assert t["turns"] == 1 and t["last_inbound_at"] and t["last_outbound_at"]


def test_health_and_a_forward_stay_responsive_during_a_slow_turn(wa, monkeypatch):
    _owner(LEAD, "us")
    _owner(OTHER, "them")
    forwarded = []
    monkeypatch.setattr(ROUTER, "_default_forward", lambda body, headers: forwarded.append(json.loads(body)) or 200)
    brain = Brain(monkeypatch)
    brain.gate = threading.Event()
    with TestClient(asgi.app) as client:
        started = time.monotonic()
        assert _post(client, "/api/wa/route-webhook", _payload(_text("Hallo", "w.us"))).status_code == 200
        assert brain.started.wait(GATE_SEC)
        assert client.get("/api/wa/health").status_code == 200
        r = _post(client, "/api/wa/route-webhook", _payload(_text("Hi", "w.them", OTHER)))
        assert r.status_code == 200 and r.json() == {"us": None, "them_forwarded": True}
        assert [f["entry"][0]["changes"][0]["value"]["messages"][0]["id"] for f in forwarded] == ["w.them"]
        assert time.monotonic() - started < GATE_SEC / 2, "nothing above waited for the held turn"
        assert brain.texts == ["Hallo"] and wa.sent == []
        brain.gate.set()
        WAPI.wait_for_background(GATE_SEC)
    assert [s["body"] for s in wa.sent] == ["re: Hallo"]


def test_a_slow_forward_does_not_block_the_event_loop(wa, monkeypatch):
    _owner(OTHER, "them")
    in_forward, release = threading.Event(), threading.Event()

    def slow_forward(body, headers):
        in_forward.set()
        assert release.wait(GATE_SEC)
        return 200

    monkeypatch.setattr(ROUTER, "_default_forward", slow_forward)
    responses = []
    with TestClient(asgi.app) as client:
        worker = threading.Thread(target=lambda: responses.append(
            _post(client, "/api/wa/route-webhook", _payload(_text("Hi", "w.them", OTHER)))))
        worker.start()
        assert in_forward.wait(GATE_SEC)
        started = time.monotonic()
        assert client.get("/api/wa/health").status_code == 200
        assert time.monotonic() - started < GATE_SEC / 2
        release.set()
        worker.join(GATE_SEC)
    assert [r.status_code for r in responses] == [200]


def test_a_forward_failure_answers_502_and_the_redelivery_answers_the_us_message_once(wa, monkeypatch):
    _owner(LEAD, "us")
    _owner(OTHER, "them")
    Brain(monkeypatch)
    calls = []

    def forward(body, headers):
        calls.append(json.loads(body))
        if len(calls) == 1:
            raise ROUTER.ForwardError("real-system webhook returned HTTP 503")
        return 200

    monkeypatch.setattr(ROUTER, "_default_forward", forward)
    body = _payload(_text("Hallo", "w.us"), _text("Hi", "w.them", OTHER),
                    statuses=[{"id": "wamid.out.9", "status": "read", "timestamp": "1", "recipient_id": LEAD}])
    with TestClient(asgi.app) as client:
        first = _post(client, "/api/wa/route-webhook", body)
        WAPI.wait_for_background(GATE_SEC)
        again = _post(client, "/api/wa/route-webhook", body)
        WAPI.wait_for_background(GATE_SEC)
    assert first.status_code == 502 and again.status_code == 200
    assert again.json()["us"]["results"] == [{"wamid": "w.us", "status": "duplicate"}]
    assert [c["entry"][0]["changes"][0]["value"]["messages"][0]["id"] for c in calls] == ["w.them", "w.them"]
    assert [s["body"] for s in wa.sent] == ["re: Hallo"], "answered by the first delivery's worker, once"
    with ST.db() as c:
        assert c.execute("select count(*) from wa_message_statuses").fetchone()[0] == 1


def test_background_turns_of_one_phone_run_in_arrival_order(wa, monkeypatch):
    brain = Brain(monkeypatch)
    brain.gate = threading.Event()
    with TestClient(asgi.app) as client:
        _post(client, "/api/wa/webhook", _payload(_text("eins", "w1")))
        assert brain.started.wait(GATE_SEC)
        _post(client, "/api/wa/webhook", _payload(_text("zwei", "w2")))
        _post(client, "/api/wa/webhook", _payload(_text("drei", "w3")))
        brain.gate.set()
        WAPI.wait_for_background(GATE_SEC)
    assert brain.texts == ["eins", "zwei", "drei"]
    assert [b for d, b in _history() if d == "out"] == ["re: eins", "re: zwei", "re: drei"] and _pending() == []


def test_a_failed_background_turn_is_recorded_the_next_message_still_runs_and_catch_up_finishes_it(wa, monkeypatch):
    brain = Brain(monkeypatch, fail_on={"eins"})
    with TestClient(asgi.app) as client:
        _post(client, "/api/wa/webhook", _payload(_text("eins", "w1"), _text("zwei", "w2")))
        WAPI.wait_for_background(GATE_SEC)
        assert [s["body"] for s in wa.sent] == ["re: zwei"] and _pending() == ["w1"]
        (row,) = client.get("/api/wa/threads").json()["rows"]
        assert row["pending_inbound"]["count"] == 1
        assert row["last_send_error"]["error"] == "inbound w1 not finished: RuntimeError: brain failed on 'eins'"

    brain.fail_on.clear()
    (result,) = CU.run(client=wa)
    assert (result["wamid"], result["status"]) == ("w1", "sent") and _pending() == []
    assert [s["body"] for s in wa.sent] == ["re: zwei", "re: eins"]


# --- recovery: the worker never ran, or died mid-turn --------------------------------------------------

def test_catch_up_answers_a_message_whose_worker_never_ran(wa, monkeypatch):
    """The process was killed after the 200: the message and its pending row are all there is."""
    Brain(monkeypatch)
    WAPI.accept_payload(_payload(_text("Hallo", "w1")))
    assert _pending() == ["w1"]
    (result,) = CU.run(client=wa)
    assert (result["phone"], result["wamid"], result["status"]) == ("+" + LEAD, "w1", "sent")
    assert _history() == [("in", "Hallo"), ("out", "re: Hallo")] and _pending() == []
    assert CU.run(client=wa) == []
    with ST.db() as c:
        t = ST.thread(c, "+" + LEAD)
    assert t["turns"] == 1 and t["last_inbound_at"]


def test_catch_up_waits_for_a_claim_in_flight_and_finishes_once_it_is_stale(wa, monkeypatch):
    """Killed inside the brain: the reply claim stays in_progress. Catch-up leaves the phone alone until the claim
    is stale, then answers."""
    Brain(monkeypatch)
    WAPI.accept_payload(_payload(_text("Hallo", "w1")))
    with ST.db() as c:
        assert ST.claim_reply_turn(c, "+" + LEAD, "w1")
    assert CU.run(client=wa) == [{"phone": "+" + LEAD, "wamid": "w1", "status": "claimed_elsewhere"}]
    assert wa.sent == [] and _pending() == ["w1"]
    monkeypatch.setattr(ST, "STALE_CLAIM_SECONDS", 0)
    assert [r["status"] for r in CU.run(client=wa)] == ["sent"] and _pending() == []


def _luna(monkeypatch, payloads):
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(CV, "extract_text_vision", lambda blob, suffix=".png", client=None: blob.decode("latin-1"))
    _classify_by_text(monkeypatch)
    monkeypatch.setattr(LB, "Client", lambda *a, **k: _PayloadLog(payloads))


def _image(wamid, media_id):
    return {"id": wamid, "from": LEAD, "type": "image", "image": {"id": media_id, "mime_type": "image/jpeg"}}


def test_catch_up_downloads_a_media_original_the_worker_never_stored(wa, monkeypatch):
    payloads = []
    _luna(monkeypatch, payloads)
    WAPI.accept_payload(_payload(_image("w.cv", "cv1")))
    meta = _meta_with(("cv1", "image/jpeg", b"Lebenslauf (Foto)"))
    (result,) = CU.run(client=meta)
    assert result["status"] == "sent" and meta.download_calls == ["https://cdn.example/cv1"]
    with ST.db() as c:
        (doc,) = ST.documents_for(c, "+" + LEAD)
        t = ST.thread(c, "+" + LEAD)
    summary = {"id": doc["id"], "document_type": "lebenslauf", "certificate_level": "unknown"}
    assert (doc["wamid"], doc["media_id"]) == ("w.cv", "cv1") and t["slots"]["documents"] == [summary]
    assert payloads[0]["documents_just_received"] == [summary] and _pending() == []


def test_catch_up_reads_a_stored_original_that_never_reached_the_card(wa, monkeypatch):
    payloads = []
    _luna(monkeypatch, payloads)
    WAPI.accept_payload(_payload(_image("w.cv", "cv1")))
    with ST.db() as c:   # the worker stored the file, then the process died before reading it
        row = c.execute("select * from wa_messages where wamid='w.cv'").fetchone()
        cdn = _meta_with(("cv1", "image/jpeg", b"Lebenslauf (Foto)"))
        WAPI._store_original(c, WAPI.message_from_row(row), client=cdn)
    no_media = FakeMeta()   # serves no media id: a second download would raise
    (result,) = CU.run(client=no_media)
    assert result["status"] == "sent" and no_media.download_calls == []
    with ST.db() as c:
        (doc,) = ST.documents_for(c, "+" + LEAD)
        assert ST.thread(c, "+" + LEAD)["slots"]["cv_text"] == "Lebenslauf (Foto)"
    assert payloads[0]["documents_just_received"] == [
        {"id": doc["id"], "document_type": "lebenslauf", "certificate_level": "unknown"}]


def test_a_stored_original_that_changed_on_disk_is_a_recorded_error_not_a_blind_reply(wa, monkeypatch):
    payloads = []
    _luna(monkeypatch, payloads)
    WAPI.accept_payload(_payload(_image("w.cv", "cv1")))
    with ST.db() as c:
        row = c.execute("select * from wa_messages where wamid='w.cv'").fetchone()
        cdn = _meta_with(("cv1", "image/jpeg", b"Lebenslauf"))
        stored = WAPI._store_original(c, WAPI.message_from_row(row), client=cdn)
        path = ST.documents_for(c, "+" + LEAD)[0]["path"]
    assert stored["id"]
    with open(path, "wb") as f:
        f.write(b"something else")
    (result,) = CU.run(client=FakeMeta())
    assert result["status"] == "error" and "no longer matches its sha256" in result["error"]
    assert payloads == [] and _pending() == ["w.cv"]


def test_catch_up_sends_the_media_ack_the_worker_never_sent_exactly_once(wa, monkeypatch):
    WAPI.accept_payload(_payload({"id": "w.voice", "from": LEAD, "type": "audio",
                                  "audio": {"id": "a1", "mime_type": "audio/ogg"}}))
    meta = _meta_with(("a1", "audio/ogg", b"OggS voice"))
    assert [r["status"] for r in CU.run(client=meta)] == ["sent"]
    assert [s["body"] for s in meta.sent] == [WAPI.MEDIA_REPLY]
    assert CU.run(client=meta) == [] and len(meta.sent) == 1


# --- the direct webhook keeps statuses and everything it does not act on ---------------------------------

def test_the_direct_webhook_stores_statuses_and_unknown_objects_and_threads_shows_them(wa, monkeypatch):
    Brain(monkeypatch)
    status = {"id": "wamid.out.1", "status": "failed", "timestamp": "1726300000", "recipient_id": LEAD,
              "errors": [{"code": 131026, "title": "Message undeliverable",
                          "error_data": {"details": "Receiver incapable"}}]}
    call = {"id": "wacid.1", "from": LEAD, "to": "4915550000000", "event": "connect", "timestamp": "1726300001",
            "direction": "USER_INITIATED"}
    body = _payload(statuses=[status], calls=[call], errors=[{"code": 1, "title": "x"}])
    body["entry"][0]["changes"].append({"field": "message_template_status_update",
                                        "value": {"event": "APPROVED", "message_template_name": "bayern_jobs"}})
    with TestClient(asgi.app) as client:
        r = _post(client, "/api/wa/webhook", body)
        assert r.status_code == 200 and (r.json()["statuses"], r.json()["events"]) == (1, 3)
        thread = client.get("/api/wa/threads", params={"phone": "+" + LEAD}).json()
    (latest,) = thread["message_statuses"]
    assert latest["status"] == "failed" and latest["errors"][0]["code"] == 131026
    assert [(e["kind"], e["raw"]) for e in thread["webhook_events"]] == [("calls", call)]
    with ST.db() as c:
        kinds = sorted(r[0] for r in c.execute("select kind from wa_webhook_events"))
        failure = ST.recent_send_failure(c, "+" + LEAD)
    assert kinds == ["calls", "errors", "message_template_status_update"]
    assert failure["error"] == "delivery failed for wamid.out.1: code 131026 Message undeliverable: Receiver incapable"


def test_a_drain_skips_a_message_the_other_process_finished_after_the_list_was_read(wa, monkeypatch):
    brain = Brain(monkeypatch)
    WAPI.accept_payload(_payload(_text("Hallo", "w1")))
    real_in_flight = ST.claim_in_flight

    def other_process_finishes_first(c, phone):
        with ST.db() as other:
            ST.finish_pending_inbound(other, "w1")
        return real_in_flight(c, phone)

    monkeypatch.setattr(ST, "claim_in_flight", other_process_finishes_first)
    assert CU.run(client=wa) == [] and brain.texts == []
