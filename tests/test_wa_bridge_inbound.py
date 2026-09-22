"""Offline tests for the phone rail's inbound door, POST /api/wa/bridge-webhook (TASK-123).

The claim under test is that this door is an adapter and nothing else: the executor pushes a
verbatim Meta envelope, so the SAME rows appear as when Meta posts the same message, dedup is the
UNIQUE ``wa_messages.wamid`` the Meta path already relies on, and nothing in ``parse_message`` /
``accept_payload`` / the background worker knows a second rail exists. Everything runs against the
real ``app.wa.asgi`` app, the one ``pflege-wa.service`` runs, so a route that is not mounted there
fails here.

``TestClient(app, client=(...))`` is what makes the network-origin half real: 127.0.0.1 is the
address an ``ssh -R`` tunnel actually gives the request, anything else is the VPN case the loopback
check exists to refuse. No network and no model: ``WA_AUTOSEND`` stays off, so the deterministic
brain's reply is recorded as a draft instead of being handed to a transport.
"""
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import api as WAPI
from app.wa import bridge_api as BAPI
from app.wa import config as C
from app.wa import store as ST
from app.wa import suppression as SUP
from app.wa.asgi import app

LEAD = "+491701234567"
META_PHONE_ID = "111222333"
BRIDGE_PHONE_ID = "pflege-bridge-01"
TOKEN = "inbound-token-aaaaaaaaaaaaaaaaaaaa"
APP_SECRET = "meta-app-secret"


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", META_PHONE_ID)
    monkeypatch.setattr(C, "BRIDGE_PHONE_NUMBER_ID", BRIDGE_PHONE_ID)
    monkeypatch.setattr(C, "BRIDGE_INBOUND_TOKEN", TOKEN)
    monkeypatch.setattr(C, "APP_SECRET", APP_SECRET)
    monkeypatch.setattr(C, "AUTOSEND", False)


def _envelope(phone_number_id, *, messages=None, statuses=None, wamid="wab.i.0001", text="Hallo"):
    """The envelope the executor pushes: Meta's own shape, field for field (plan §5.3)."""
    value = {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": "4915216678689", "phone_number_id": phone_number_id},
             "contacts": [{"profile": {"name": "Ivan"}, "wa_id": LEAD.lstrip("+")}]}
    if statuses is None:
        value["messages"] = messages if messages is not None else [
            {"from": LEAD.lstrip("+"), "id": wamid, "timestamp": "1789312500", "type": "text",
             "text": {"body": text}}]
    else:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "pflege-bridge", "changes": [{"field": "messages", "value": value}]}]}


def _bridge_post(payload, *, token=TOKEN, host="127.0.0.1", headers=None):
    with TestClient(app, client=(host, 41234)) as client:
        sent = {BAPI.TOKEN_HEADER: token, BAPI.DELIVERY_HEADER: "9d1f-uuid", **(headers or {})}
        if token is None:
            sent.pop(BAPI.TOKEN_HEADER)
        return client.post("/api/wa/bridge-webhook", content=json.dumps(payload),
                           headers={"Content-Type": "application/json", **sent})


def _meta_post(payload):
    """The same message through Meta's own signed route, for the row-by-row comparison."""
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    with TestClient(app, client=("127.0.0.1", 41234)) as client:
        return client.post("/api/wa/webhook", content=body,
                           headers={"Content-Type": "application/json", "X-Hub-Signature-256": signature})


def _inbound_rows(c):
    rows = c.execute("select phone, direction, wamid, body, kind, meta from wa_messages where direction='in' "
                     "order by id").fetchall()
    return [dict(r) for r in rows]


def _pending(c):
    rows = c.execute("select wamid, phone, attempts, last_error from wa_inbound_pending order by wamid").fetchall()
    return [dict(r) for r in rows]


# --- the same rows as Meta (AC#1) ------------------------------------------------------------------

def test_a_bridge_payload_produces_the_same_rows_as_the_meta_webhook(wa, tmp_path, monkeypatch):
    """The whole design in one assertion: if these two diverge, the executor is not speaking Meta's
    envelope and every parse/accept/worker test above this line is testing a different code path."""
    resp = _bridge_post(_envelope(BRIDGE_PHONE_ID))
    assert resp.status_code == 200 and resp.json()["handled"] == 1
    WAPI.wait_for_background(timeout=60)
    with ST.db() as c:
        from_bridge, pending_from_bridge = _inbound_rows(c), _pending(c)

    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "meta.sqlite")   # a fresh database for the Meta run
    assert _meta_post(_envelope(META_PHONE_ID)).status_code == 200
    WAPI.wait_for_background(timeout=60)
    with ST.db() as c:
        from_meta, pending_from_meta = _inbound_rows(c), _pending(c)

    assert from_bridge == from_meta != []
    assert [p["wamid"] for p in pending_from_bridge] == [p["wamid"] for p in pending_from_meta]


def test_the_message_reaches_the_thread_and_is_answered_by_the_ordinary_worker(wa):
    """Not just stored: the same background worker picks the phone up, the deterministic brain
    answers, and with WA_AUTOSEND off the reply lands as a draft. No rail-specific path anywhere."""
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID)).status_code == 200
    WAPI.wait_for_background(timeout=60)
    with ST.db() as c:
        history = ST.history(c, LEAD)
        assert ST.pending_inbound_summary(c, LEAD) is None, "the pending row is finished by the worker"
    assert [m["direction"] for m in history][0] == "in"
    assert [m for m in history if m["direction"] == "out"], "the thread was answered"


# --- dedup is the UNIQUE wamid, not a new table (AC#2) --------------------------------------------

def test_the_same_payload_twice_yields_exactly_one_row(wa):
    first = _bridge_post(_envelope(BRIDGE_PHONE_ID))
    WAPI.wait_for_background(timeout=60)
    second = _bridge_post(_envelope(BRIDGE_PHONE_ID))
    WAPI.wait_for_background(timeout=60)
    assert [r["status"] for r in first.json()["results"]] == ["accepted"]
    assert [r["status"] for r in second.json()["results"]] == ["duplicate"]
    with ST.db() as c:
        assert len(_inbound_rows(c)) == 1
        assert {r[0] for r in c.execute("select name from sqlite_master where type='table'").fetchall()} \
            .isdisjoint({"wa_bridge_inbound", "wa_bridge_dedup"}), "dedup is the UNIQUE wamid, not a second table"


def test_two_different_inbound_ids_are_two_rows(wa):
    """The flip side: dedup keys on the id the executor mints, so two real messages stay two."""
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID, wamid="wab.i.0001", text="Hallo")).status_code == 200
    WAPI.wait_for_background(timeout=60)
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID, wamid="wab.i.0002", text="Noch eine")).status_code == 200
    WAPI.wait_for_background(timeout=60)
    with ST.db() as c:
        assert [r["wamid"] for r in _inbound_rows(c)] == ["wab.i.0001", "wab.i.0002"]


# --- the trust boundary (AC#3) ---------------------------------------------------------------------

def test_a_non_loopback_caller_is_refused_even_with_the_right_token(wa):
    """The ssh tunnel is what makes the caller 127.0.0.1; a VPN-sourced POST is the case this
    refuses, and it is the reason the plan picked ssh over WireGuard."""
    resp = _bridge_post(_envelope(BRIDGE_PHONE_ID), host="10.8.0.4")
    assert resp.status_code == 403
    with ST.db() as c:
        assert _inbound_rows(c) == [], "a refused call records nothing at all"


@pytest.mark.parametrize("token", [None, "", "wrong-token-aaaaaaaaaaaaaaaaaaaaa",
                                   TOKEN[:-1],            # a prefix of the real one
                                   TOKEN + "x",           # the real one plus a tail
                                   TOKEN.upper()])
def test_a_bad_or_missing_token_is_refused(wa, token):
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID), token=token).status_code == 403
    with ST.db() as c:
        assert _inbound_rows(c) == []


def test_an_unconfigured_token_shuts_the_door_rather_than_opening_it(wa, monkeypatch):
    """The empty-string trap: comparing "" with a missing header would succeed and let an
    unauthenticated payload into a real conversation."""
    monkeypatch.setattr(C, "BRIDGE_INBOUND_TOKEN", "")
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID), token=None).status_code == 403
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID), token="").status_code == 403


def test_the_token_is_compared_in_constant_time(wa, monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(BAPI.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID)).status_code == 200
    assert calls == [(TOKEN, TOKEN)], "the token must go through hmac.compare_digest, not =="


def test_the_inbound_token_is_not_the_outbound_one(wa, monkeypatch):
    """Two secrets, two directions: a leak of the server-to-executor bearer must not open this door."""
    monkeypatch.setattr(C, "BRIDGE_TOKEN", "outbound-token-bbbbbbbbbbbbbbbbbb")
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID), token=C.BRIDGE_TOKEN).status_code == 403


# --- statuses on the same endpoint (AC#4) ----------------------------------------------------------

def test_a_bridge_status_is_recorded_like_a_meta_one_and_keeps_our_own_error_slug(wa):
    statuses = [{"id": "wab.o.deadbeef", "recipient_id": LEAD.lstrip("+"), "status": "failed",
                 "timestamp": "1789312500",
                 "errors": [{"code": "wab-blocked", "title": "Recipient cannot receive this message",
                             "error_data": {"details": "bridge: compose box absent"}}]}]
    resp = _bridge_post(_envelope(BRIDGE_PHONE_ID, statuses=statuses))
    assert resp.status_code == 200 and resp.json()["statuses"] == 1
    with ST.db() as c:
        latest = ST.latest_message_status(c, "wab.o.deadbeef")
        failure = ST.recent_send_failure(c, LEAD)
    assert latest["status"] == "failed" and latest["errors"][0]["code"] == "wab-blocked"
    assert "wab-blocked" in failure["error"], "a bridge failure keeps our slug, not a forged Meta numeric"
    assert "131" not in failure["error"]


# --- which number is ours (AC#5) --------------------------------------------------------------------

def test_both_phone_number_ids_are_accepted_and_the_meta_one_stays_set(wa):
    assert C.PHONE_NUMBER_ID == META_PHONE_ID, "blanking the Meta id would make every number match"
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID, wamid="wab.i.bridge")).status_code == 200
    WAPI.wait_for_background(timeout=60)
    assert _meta_post(_envelope(META_PHONE_ID, wamid="wamid.meta")).status_code == 200
    WAPI.wait_for_background(timeout=60)
    with ST.db() as c:
        assert [r["wamid"] for r in _inbound_rows(c)] == ["wab.i.bridge", "wamid.meta"]


def test_a_third_phone_number_id_is_still_not_ours_to_answer(wa):
    resp = _bridge_post(_envelope("some-other-number", wamid="wab.i.foreign"))
    assert resp.status_code == 200 and resp.json()["skipped"] == 1 and resp.json()["handled"] == 0
    with ST.db() as c:
        assert _inbound_rows(c) == [], "stored raw as a webhook event, never answered"
        assert c.execute("select count(*) as n from wa_webhook_events").fetchone()["n"] == 1


# --- suppression is a property of the human, not of the rail (mission item 5) -----------------------

def test_a_suppressed_number_is_refused_on_this_rail_too(wa):
    with ST.db() as c:
        SUP.suppress(c, LEAD, SUP.REASON_STOP, "meta", trigger_text="STOP")
    assert _bridge_post(_envelope(BRIDGE_PHONE_ID)).status_code == 200
    WAPI.wait_for_background(timeout=60)
    with ST.db() as c:
        outbound = c.execute("select count(*) as n from wa_messages where direction='out'").fetchone()["n"]
        failure = ST.recent_send_failure(c, LEAD)
        assert _inbound_rows(c), "the message itself is still stored -- it is the record of what they said"
    assert outbound == 0, "not even a draft: a refusal must not look like a send that could be flushed later"
    assert "suppressed recipient" in failure["error"]


# --- the health proxy (AC#6) -------------------------------------------------------------------------

def test_bridge_health_answers_loudly_when_the_rail_is_not_configured(wa, monkeypatch):
    monkeypatch.setattr(C, "BRIDGE_URL", "")
    monkeypatch.setattr(C, "BRIDGE_TOKEN", "")
    with TestClient(app, client=("127.0.0.1", 41234)) as client:
        body = client.get("/api/wa/bridge-health").json()
    assert body["ok"] is False and "WA_BRIDGE_URL" in body["error"]


def test_bridge_health_reports_what_the_executor_says(wa, monkeypatch):
    monkeypatch.setattr(C, "BRIDGE_URL", "http://127.0.0.1:8793")
    monkeypatch.setattr(C, "BRIDGE_TOKEN", "outbound-token")
    seen = {}

    def fake_transport(method, url, headers=None, data=None, timeout=None):
        seen.update(method=method, url=url, auth=headers.get("Authorization"))
        return 200, {"ok": True, "queue": {"queued": 0}, "inbound": {"count": 0}}

    monkeypatch.setattr(BAPI.BR, "_default_transport", fake_transport)
    with TestClient(app, client=("127.0.0.1", 41234)) as client:
        body = client.get("/api/wa/bridge-health").json()
    assert seen == {"method": "GET", "url": "http://127.0.0.1:8793/v1/health",
                    "auth": "Bearer outbound-token"}
    assert body["ok"] is True and body["health"]["queue"] == {"queued": 0}
