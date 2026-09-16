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


# --- TASK-99: every object lands in exactly one half; the us half is recorded, turns run in the background --

US, THEM = "491111111111", "492222222222"
BUSINESS = "4915550000000"


def _change(field="messages", **value):
    return {"field": field, "value": {"messaging_product": "whatsapp",
                                      "metadata": {"display_phone_number": BUSINESS, "phone_number_id": PHONE_ID},
                                      **value}}


def _body(*changes, **top):
    return json.dumps({"object": "whatsapp_business_account",
                       "entry": [{"id": "waba", "changes": list(changes)}], **top}).encode()


def _status(wamid, phone, status, ts="1726300000", **extra):
    return {"id": wamid, "status": status, "timestamp": ts, "recipient_id": phone, **extra}


FAILED = _status("wamid.out.us", US, "failed", ts="1726300010", errors=[{
    "code": 131047, "title": "Re-engagement message", "message": "Re-engagement message",
    "error_data": {"details": "Message failed to send because more than 24 hours have passed."}}])


@pytest.fixture()
def owners(wa, monkeypatch):
    _mark_owner("+" + US, "us")
    _mark_owner("+" + THEM, "them")
    monkeypatch.setattr(C, "REAL_SYSTEM_WEBHOOK_URL", "https://real-system.example/webhook")
    return wa


def _route(body, forwarded, meta):
    result = ROUTER.route_webhook(body, _sign(body), meta_client=meta, forward=lambda b, h: forwarded.append(
        {"payload": json.loads(b), "valid_signature": h["X-Hub-Signature-256"] == _sign(b)}))
    from app.wa import api as WAPI
    WAPI.wait_for_background(timeout=30)
    return result


def _db(sql, *args):
    with ST.db() as c:
        return [tuple(r) for r in c.execute(sql, args).fetchall()]


def test_a_status_only_payload_for_an_us_phone_is_stored_not_forwarded(owners):
    delivered = _status("wamid.out.us", US, "delivered", ts="1726300005",
                        conversation={"id": "conv-1", "origin": {"type": "marketing"}},
                        pricing={"billable": True, "pricing_model": "PMP", "category": "marketing"})
    forwarded = []
    body = _body(_change(statuses=[_status("wamid.out.us", US, "sent"), delivered, FAILED]))
    result = _route(body, forwarded, owners)
    assert forwarded == [] and result["us"]["statuses"] == 3 and result["us"]["handled"] == 0
    with ST.db() as c:
        latest = ST.latest_message_status(c, "wamid.out.us")
        assert latest["status"] == "failed" and latest["errors"][0]["code"] == 131047
        (row,) = [s for s in ST.latest_message_statuses_for(c, "+" + US)]
        assert row["id"] == latest["id"]
        failure = ST.recent_send_failure(c, "+" + US)
    assert "code 131047 Re-engagement message: Message failed to send because more than 24 hours" in failure["error"]
    assert _db("select status, conversation, pricing from wa_message_statuses where status='delivered'") == [
        ("delivered", json.dumps(delivered["conversation"]), json.dumps(delivered["pricing"]))]
    assert owners.sent == []


def test_a_redelivered_failed_status_is_stored_and_recorded_once(owners):
    body = _body(_change(statuses=[FAILED]))
    _route(body, [], owners)
    _route(body, [], owners)
    assert _db("select count(*) from wa_message_statuses") == [(1,)]
    assert _db("select count(*) from wa_send_failures") == [(1,)]


def test_a_status_only_payload_for_a_them_phone_is_forwarded_not_stored(owners):
    forwarded = []
    status = _status("wamid.out.them", THEM, "read")
    result = _route(_body(_change(statuses=[status])), forwarded, owners)
    assert result == {"us": None, "them_forwarded": True}
    (fwd,) = forwarded
    assert fwd["valid_signature"] and fwd["payload"]["entry"][0]["changes"][0]["value"]["statuses"] == [status]
    assert _db("select count(*) from wa_message_statuses") == [(0,)]


def test_a_mixed_change_puts_every_message_status_and_contact_in_exactly_one_half(owners):
    us_msg, them_msg = _msg("hallo", "w.us", US), _msg("hi", "w.them", THEM)
    us_status, them_status = _status("wamid.out.us", US, "read"), _status("wamid.out.them", THEM, "delivered")
    contacts = [{"profile": {"name": "Anna"}, "wa_id": US}, {"profile": {"name": "Olga"}, "wa_id": THEM}]
    forwarded = []
    body = _body(_change(contacts=contacts, messages=[us_msg, them_msg], statuses=[them_status, us_status]))
    result = _route(body, forwarded, owners)
    (fwd,) = forwarded
    (change,) = fwd["payload"]["entry"][0]["changes"]
    assert change["field"] == "messages" and change["value"]["metadata"]["phone_number_id"] == PHONE_ID
    assert {k: v for k, v in change["value"].items() if k not in ("messaging_product", "metadata")} == {
        "contacts": [contacts[1]], "messages": [them_msg], "statuses": [them_status]}
    assert result["us"]["handled"] == 1 and result["us"]["statuses"] == 1
    assert _db("select wamid from wa_message_statuses") == [("wamid.out.us",)]
    assert _db("select phone, kind from wa_webhook_events") == [("+" + US, "contacts")]
    with ST.db() as c:
        assert [m["direction"] for m in ST.history(c, "+" + US)][:1] == ["in"]
        assert ST.history(c, "+" + THEM) == []
    assert owners.sent and {s["to"] for s in owners.sent} == {"+" + US}, "the us message was answered"


def test_every_call_goes_to_the_real_system_and_a_phone_we_own_keeps_a_raw_copy(owners):
    """The real system is the only call bridge: its manager CRM places calls and reads the SDP answer from the calls
    webhook, a missed candidate call flags a manager there. A call for a phone we own is forwarded too."""
    us_call = {"id": "wacid.1", "from": US, "to": BUSINESS, "event": "connect", "timestamp": "1726300000",
               "direction": "USER_INITIATED"}
    them_call = {"id": "wacid.2", "from": BUSINESS, "to": THEM, "event": "terminate", "status": "Completed",
                 "timestamp": "1726300001", "direction": "BUSINESS_INITIATED", "duration": 30}
    no_direction = {"id": "wacid.3", "from": US, "to": BUSINESS, "event": "connect", "timestamp": "1726300002"}
    forwarded = []
    result = _route(_body(_change("calls", calls=[us_call, them_call, no_direction])), forwarded, owners)
    (fwd,) = forwarded
    assert fwd["payload"]["entry"][0]["changes"][0]["value"]["calls"] == [us_call, them_call, no_direction]
    assert result["us"]["events"] == 1
    events = _db("select phone, field, kind, raw from wa_webhook_events")
    assert [(p, f, k, json.loads(r)) for p, f, k, r in events] == [("+" + US, "calls", "calls", us_call)]


def test_a_crm_call_to_a_campaign_phone_reaches_the_real_system_with_its_statuses(owners):
    """Repro of the review finding: a phone flipped to us by a campaign. The CRM's business-initiated connect (SDP
    answer), its ACCEPTED call status and a call-permission reply all reach the real system; a delivery status of a
    message wamid we do not hold stays with the phone's owner (us)."""
    with R.db() as c:
        c.execute("update wa_ownership set reason='campaign:bayern-test' where phone=?", ("+" + US,))
        c.commit()
    connect = {"id": "wacid.crm", "from": BUSINESS, "to": US, "event": "connect", "timestamp": "1726300000",
               "direction": "BUSINESS_INITIATED", "session": {"sdp_type": "answer", "sdp": "v=0 synthetic"}}
    accepted = {"id": "wacid.crm", "type": "call", "status": "ACCEPTED", "timestamp": "1726300001",
                "recipient_id": US}
    permission = {"id": "wamid.perm", "from": US, "type": "interactive", "timestamp": "1726300002",
                  "interactive": {"type": "call_permission_reply",
                                  "call_permission_reply": {"response": "accept", "is_permanent": False}}}
    crm_read = _status("wamid.crm.text", US, "read", ts="1726300003")
    forwarded = []
    result = _route(_body(_change("calls", calls=[connect]),
                          _change(statuses=[accepted, crm_read], messages=[permission])), forwarded, owners)
    (fwd,) = forwarded
    values = [c["value"] for c in fwd["payload"]["entry"][0]["changes"]]
    assert values[0]["calls"] == [connect]
    assert values[1]["statuses"] == [accepted] and values[1]["messages"] == [permission]
    assert result["us"]["statuses"] == 1 and result["us"]["events"] == 3 and result["us"]["handled"] == 0
    assert _db("select wamid, status from wa_message_statuses") == [("wamid.crm.text", "read")]
    assert sorted(_db("select phone, kind from wa_webhook_events")) == [
        ("+" + US, "calls"), ("+" + US, "messages"), ("+" + US, "statuses")]
    assert owners.sent == []


def test_unknown_fields_and_keys_are_forwarded_unchanged(owners):
    template_update = {"field": "message_template_status_update", "value": {
        "event": "APPROVED", "message_template_id": 123, "message_template_name": "bayern_jobs",
        "message_template_language": "de", "reason": "NONE"}}
    errors = [{"code": 131000, "title": "Something went wrong", "message": "Something went wrong"}]
    other_number = {"field": "messages", "value": {"messaging_product": "whatsapp",
                                                   "metadata": {"phone_number_id": "999"},
                                                   "messages": [_msg("hallo", "w.other", US)]}}
    forwarded = []
    body = _body(template_update, _change(errors=errors, messages=[_msg("hallo", "w.us", US)]), other_number,
                 extra_top_level={"x": 1})
    result = _route(body, forwarded, owners)
    (fwd,) = forwarded
    changes = fwd["payload"]["entry"][0]["changes"]
    assert changes[0] == template_update and changes[2] == other_number
    assert changes[1]["value"] == {"messaging_product": "whatsapp", "errors": errors,
                                   "metadata": {"display_phone_number": BUSINESS, "phone_number_id": PHONE_ID}}
    assert fwd["payload"]["extra_top_level"] == {"x": 1}
    assert result["us"]["handled"] == 1 and _db("select count(*) from wa_webhook_events") == [(0,)]


def test_an_us_message_nothing_answers_is_kept_raw(owners):
    system = {"id": "w.system", "from": US, "type": "system",
              "system": {"body": "User changed number", "type": "user_changed_number", "wa_id": "491111111112"}}
    result = _route(_body(_change(messages=[system])), [], owners)
    assert result["us"]["skipped"] == 1 and result["us"]["events"] == 1
    assert [(p, k, json.loads(r)) for p, k, r in _db("select phone, kind, raw from wa_webhook_events")] == [
        ("+" + US, "messages", system)]
    assert owners.sent == []


def test_a_contacts_only_change_is_split_by_wa_id(owners):
    contacts = [{"profile": {"name": "Anna"}, "wa_id": US}, {"profile": {"name": "Olga"}, "wa_id": THEM}]
    forwarded = []
    _route(_body(_change(contacts=contacts)), forwarded, owners)
    assert forwarded[0]["payload"]["entry"][0]["changes"][0]["value"]["contacts"] == [contacts[1]]
    assert _db("select phone, kind from wa_webhook_events") == [("+" + US, "contacts")]


def test_a_status_call_or_contact_never_decides_a_new_phones_owner(owners, tmp_path, monkeypatch):
    """Only a message runs route_decision. The known-phones export lags: a status of the real system's outbound
    message to a brand-new number must go to the real system and must not record that phone as ours."""
    known = tmp_path / "known.txt"
    known.write_text("", encoding="utf-8")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known))
    fresh = "493333333333"
    status = _status("wamid.out.real", fresh, "delivered")
    call = {"id": "wacid.9", "from": fresh, "to": BUSINESS, "event": "connect", "timestamp": "1",
            "direction": "USER_INITIATED"}
    forwarded = []
    _route(_body(_change(statuses=[status], contacts=[{"profile": {"name": "Neu"}, "wa_id": fresh}]),
                 _change("calls", calls=[call])), forwarded, owners)
    values = [c["value"] for c in forwarded[0]["payload"]["entry"][0]["changes"]]
    assert values[0]["statuses"] == [status] and values[1]["calls"] == [call]
    assert _db("select count(*) from wa_ownership where phone=?", "+" + fresh) == [(0,)]


def test_a_status_of_our_own_outbound_message_is_ours_without_an_ownership_record(owners):
    with ST.db() as c:
        ST.record_outbound(c, "+493333333333", "wamid.out.ours", "[template:bayern_jobs]", kind="template")
    forwarded = []
    _route(_body(_change(statuses=[_status("wamid.out.ours", "493333333333", "read")])), forwarded, owners)
    assert forwarded == [] and _db("select wamid, status from wa_message_statuses") == [("wamid.out.ours", "read")]


def test_a_new_leads_contacts_follow_its_message_even_when_listed_first(owners, tmp_path, monkeypatch):
    known = tmp_path / "known.txt"
    known.write_text("", encoding="utf-8")
    monkeypatch.setattr(C, "REAL_SYSTEM_PHONES_FILE", str(known))
    lead = "494444444444"
    contact = {"profile": {"name": "Lea"}, "wa_id": lead}
    forwarded = []
    result = _route(_body(_change(contacts=[contact], messages=[_msg("Hallo", "w.lead", lead)])), forwarded, owners)
    assert forwarded == [] and result["us"]["handled"] == 1
    assert _db("select phone, kind from wa_webhook_events") == [("+" + lead, "contacts")]
