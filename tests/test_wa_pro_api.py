"""Tests for app/wa/pro_api.py (TASK-395/396, Ivan 2026-09-29/30): the bearer-token Pro read API and
Daria's handoff write-back. All offline -- one tmp sqlite per test (app/wa/config.py:SQLITE_PATH
monkeypatched, the same pattern tests/test_wa_queue.py already uses), a synthetic app.data snapshot,
and WA_SALES_BRAIN_PATH always pointed at a synthetic tmp file or a deliberately-missing path --
never the colleague's real /opt/clinic-dispatcher/var/sales_brain.sqlite.
"""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import asgi
from app.wa import config as C
from app.wa import phones as P
from app.wa import pro_api as PA
from app.wa import queue as Q
from app.wa import store as ST
from app.wa.luna import escalation as ESC

READ_TOKEN = "test-read-token"
WRITE_TOKEN = "test-write-token"
RH = {"Authorization": f"Bearer {READ_TOKEN}"}
WH = {"Authorization": f"Bearer {WRITE_TOKEN}"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """One tmp sqlite, a synthetic clinic snapshot, both pro tokens set, and sales_brain pointed at a
    path that deliberately does not exist -- a test that wants sales_brain data builds its own file
    and overrides WA_SALES_BRAIN_PATH itself (see the leads tests below)."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setenv("WA_API_TOKEN", READ_TOKEN)
    monkeypatch.setenv("WA_API_WRITE_TOKEN", WRITE_TOKEN)
    monkeypatch.setenv("WA_SALES_BRAIN_PATH", str(tmp_path / "no-such-sales-brain.sqlite"))
    clinics = [{"clinic_id": "c1", "name": "Klinikum Test", "town": "Testort"},
               {"clinic_id": "c2", "name": "Klinikum Zwei", "town": "Zweistadt"}]
    D._snap.update({"at": time.time(), "jobs": [], "clinics": clinics,
                    "by_clinic": {c["clinic_id"]: c for c in clinics}, "facets": {}, "taxonomy": {},
                    "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    return tmp_path


@pytest.fixture()
def client(env):
    return TestClient(asgi.app)


def _seed_thread(phone="+491701234560"):
    with ST.db() as c:
        ST.thread(c, phone)
        ST.record_inbound(c, phone, f"wamid-in-{phone}", "Hallo", kind="text")
        ST.record_outbound(c, phone, f"wamid-out-{phone}", "Willkommen", kind="text")
    return phone


# --- auth --------------------------------------------------------------------------------------

def test_read_route_503_when_no_token_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.delenv("WA_API_TOKEN", raising=False)
    monkeypatch.delenv("WA_API_WRITE_TOKEN", raising=False)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    r = TestClient(asgi.app).get("/api/wa/pro/threads", headers=RH)
    assert r.status_code == 503
    assert r.json()["detail"] == "pro api not configured"


def test_read_route_401_without_token(client):
    assert client.get("/api/wa/pro/threads").status_code == 401


def test_read_route_401_with_wrong_token(client):
    r = client.get("/api/wa/pro/threads", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_read_route_ok_with_read_token(client):
    assert client.get("/api/wa/pro/threads", headers=RH).status_code == 200


def test_read_route_ok_with_write_token_too(client):
    """The write token may also read (design decision 2)."""
    assert client.get("/api/wa/pro/threads", headers=WH).status_code == 200


def test_write_route_401_with_read_token(client):
    """A read token can never write."""
    _seed_thread()
    r = client.post("/api/wa/pro/handoffs", headers=RH,
                    json={"thread_id": "whatever", "clinic_id": "c1", "status": "sent_to_clinic",
                          "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 401


def test_write_route_503_when_write_token_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setenv("WA_API_TOKEN", READ_TOKEN)
    monkeypatch.delenv("WA_API_WRITE_TOKEN", raising=False)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    r = TestClient(asgi.app).post("/api/wa/pro/handoffs", headers=RH,
                                  json={"thread_id": "x", "clinic_id": "c1", "status": "sent_to_clinic",
                                        "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 503


def test_write_route_ok_with_write_token(client):
    phone = _seed_thread()
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                          "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 200
    assert r.json()["applied"] is True


# --- phone masking / raw phone never leaked -----------------------------------------------------

@pytest.mark.parametrize("raw,expected_tail", [
    ("+491701234567", "4567"),
    ("+4917012345678", "5678"),
    ("+12025550123", "0123"),
    ("+79161234567", "4567"),
    ("+436601234567", "4567"),
])
def test_phone_masked_keeps_only_last_four_digits(raw, expected_tail):
    masked = P.phone_masked(raw)
    digits_in_masked = "".join(ch for ch in masked if ch.isdigit())
    assert digits_in_masked.endswith(expected_tail)
    # Everything between the leading "+<country code>" and the trailing 4 digits must be bullets,
    # never a real digit -- the contract's own privacy invariant (design decision 4).
    body = masked.split(" ", 1)[1].rsplit(" ", 1)[0]
    assert all(ch == "•" for ch in body if ch not in " ")


def test_phone_masked_none_for_empty():
    assert P.phone_masked("") is None
    assert P.phone_masked(None) is None


def test_raw_phone_never_appears_in_any_pro_response(client):
    phone = _seed_thread("+491709998877")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})
    bodies = []
    for path in ("/api/wa/pro/threads", f"/api/wa/pro/threads/{tid}",
                 f"/api/wa/pro/threads/{tid}/messages", "/api/wa/pro/health",
                 f"/api/wa/pro/handoffs?thread_id={tid}", "/api/wa/pro/leads"):
        bodies.append(client.get(path, headers=RH).text)
    full = "\n".join(bodies)
    raw_digits = phone.lstrip("+")
    assert raw_digits not in full, "raw phone leaked into a Pro API response body"


# --- threads listing / paging -------------------------------------------------------------------

def test_threads_excludes_test_threads_by_default(client):
    phone = _seed_thread()
    with ST.db() as c:
        ST.mark_test_thread(c, phone, True)
    r = client.get("/api/wa/pro/threads", headers=RH)
    assert r.json()["total"] == 0
    r = client.get("/api/wa/pro/threads?include_test=1", headers=RH)
    assert r.json()["total"] == 1
    assert r.json()["test_threads"] == 1


def test_threads_envelope_shape_and_source(client):
    _seed_thread()
    r = client.get("/api/wa/pro/threads", headers=RH)
    body = r.json()
    assert body["source"].startswith("harness@")
    assert set(body) == {"total", "limit", "offset", "next_offset", "test_threads", "generated_at",
                         "source", "rows"}


def test_threads_paging_reaches_the_end_with_null_next_offset(client):
    for i in range(5):
        _seed_thread(f"+49170000{i:04d}")
    r = client.get("/api/wa/pro/threads?limit=2&offset=0", headers=RH)
    body = r.json()
    assert len(body["rows"]) == 2
    assert body["next_offset"] == 2
    r = client.get("/api/wa/pro/threads?limit=2&offset=4", headers=RH)
    body = r.json()
    assert len(body["rows"]) == 1
    assert body["next_offset"] is None


def test_threads_paging_has_no_cap_above_the_default_limit(client):
    """CLAUDE.md "no safety nets": a caller asking for more than DEFAULT_THREADS_LIMIT gets it all,
    not silently clamped to the default."""
    n = PA.DEFAULT_THREADS_LIMIT + 5
    for i in range(n):
        _seed_thread(f"+4915{i:08d}")
    r = client.get(f"/api/wa/pro/threads?limit={n}", headers=RH)
    body = r.json()
    assert body["total"] == n
    assert len(body["rows"]) == n
    assert body["next_offset"] is None


def test_unknown_thread_id_is_404(client):
    assert client.get("/api/wa/pro/threads/t_doesnotexist", headers=RH).status_code == 404
    assert client.get("/api/wa/pro/threads/t_doesnotexist/messages", headers=RH).status_code == 404


def test_thread_id_stable_across_requests_and_never_derived_from_phone(client):
    phone = _seed_thread("+491701111111")
    r1 = client.get("/api/wa/pro/threads", headers=RH).json()["rows"][0]["thread_id"]
    r2 = client.get("/api/wa/pro/threads", headers=RH).json()["rows"][0]["thread_id"]
    assert r1 == r2
    assert r1.startswith("t_")
    assert "1701111111" not in r1


# --- thread detail: documents whitelist -----------------------------------------------------------

def test_thread_detail_documents_are_metadata_only(client):
    phone = _seed_thread()
    with ST.db() as c:
        ST.record_document(c, phone, "wamid-doc-1", "media-1", "document", "application/pdf",
                           "lebenslauf.pdf", "/data/wa_media/somewhere/secret.pdf", "deadbeef" * 8, 12345)
        tid = ST.thread_id_for_phone(c, phone)
    body = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()
    assert len(body["documents"]) == 1
    doc = body["documents"][0]
    assert set(doc) == set(PA.DOCUMENT_FIELDS)
    for forbidden in ("path", "sha256", "media_id", "original_filename", "text", "text_key",
                      "import_source", "import_ref"):
        assert forbidden not in doc


# --- messages: paging, tombstones, no wamid --------------------------------------------------------

def test_messages_no_cursor_returns_newest_limit_ascending(client):
    phone = _seed_thread("+491702222222")
    with ST.db() as c:
        for i in range(3, 6):
            ST.record_inbound(c, phone, f"wamid-in-{i}", f"msg {i}", kind="text")
        tid = ST.thread_id_for_phone(c, phone)
    r = client.get(f"/api/wa/pro/threads/{tid}/messages?limit=2", headers=RH)
    body = r.json()
    assert len(body["rows"]) == 2
    ids = [row["id"] for row in body["rows"]]
    assert ids == sorted(ids)   # ascending


def test_messages_no_wamid_field_ever(client):
    phone = _seed_thread("+491702222223")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    body = client.get(f"/api/wa/pro/threads/{tid}/messages", headers=RH).json()
    for row in body["rows"]:
        assert "wamid" not in row


def test_messages_deleted_row_is_a_tombstone(client):
    phone = "+491702222224"
    with ST.db() as c:
        ST.thread(c, phone)
        ST.record_inbound(c, phone, "wamid-del-1", "to be forgotten", kind="text")
        ST.forget_message(c, "wamid-del-1")
        tid = ST.thread_id_for_phone(c, phone)
    body = client.get(f"/api/wa/pro/threads/{tid}/messages", headers=RH).json()
    row = body["rows"][0]
    assert row["kind"] == "deleted"
    assert row["body"] is None
    assert row["deleted"] is True


def test_messages_before_id_and_after_id_page_correctly(client):
    phone = "+491702222225"
    with ST.db() as c:
        ST.thread(c, phone)
        for i in range(5):
            ST.record_inbound(c, phone, f"wamid-p-{i}", f"m{i}", kind="text")
        tid = ST.thread_id_for_phone(c, phone)
    full = client.get(f"/api/wa/pro/threads/{tid}/messages?limit=10", headers=RH).json()["rows"]
    mid_id = full[2]["id"]
    older = client.get(f"/api/wa/pro/threads/{tid}/messages?before_id={mid_id}&limit=10",
                       headers=RH).json()["rows"]
    assert all(row["id"] < mid_id for row in older)
    newer = client.get(f"/api/wa/pro/threads/{tid}/messages?after_id={mid_id}", headers=RH).json()["rows"]
    assert all(row["id"] > mid_id for row in newer)


def test_messages_before_id_and_after_id_together_is_400(client):
    phone = _seed_thread("+491702222226")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    r = client.get(f"/api/wa/pro/threads/{tid}/messages?before_id=1&after_id=2", headers=RH)
    assert r.status_code == 400


# --- health ---------------------------------------------------------------------------------------

def test_health_ok(client):
    r = client.get("/api/wa/pro/health", headers=RH)
    assert r.status_code == 200
    assert "rails" in r.json()


# --- escalated_at: stamped once, never overwritten --------------------------------------------------

def test_escalated_at_stamped_once_and_never_overwritten():
    card = {}
    ESC.record_escalation(card, "explicit_human_request", "wants a human")
    first_stamp = card["_escalated_at"]
    assert first_stamp
    time.sleep(0.01)
    ESC.record_escalation(card, "pet_policy_question", "second reason")
    assert card["_escalated_at"] == first_stamp


def test_escalated_at_null_until_first_escalation(client):
    phone = _seed_thread("+491703333330")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    row = client.get("/api/wa/pro/threads", headers=RH).json()["rows"][0]
    assert row["escalated_at"] is None
    with ST.db() as c:
        t = ST.thread(c, phone)
        ESC.record_escalation(t["slots"], "explicit_human_request", "please")
        ST.save_thread(c, t)
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["escalated_at"] is not None
    assert row["escalation_codes"] == ["explicit_human_request"]


def test_escalated_at_is_code_owned_protected_from_card_patch():
    from app.wa import luna_brain as LB
    assert "_escalated_at" in LB.CODE_OWNED_CARD_KEYS


# --- handoff write-back: validation ------------------------------------------------------------------

def test_handoff_write_requires_thread_id_or_crm_candidate_id(client):
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"clinic_id": "c1", "status": "sent_to_clinic", "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 400


def test_handoff_write_requires_clinic_id_or_clinic_name(client):
    phone = _seed_thread("+491704444440")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "status": "sent_to_clinic", "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 400


def test_handoff_write_unknown_status_is_400(client):
    phone = _seed_thread("+491704444441")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "clinic_id": "c1", "status": "made_up_status",
                          "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 400


def test_handoff_write_unknown_thread_id_is_404(client):
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": "t_nope", "clinic_id": "c1", "status": "sent_to_clinic",
                          "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 404


def test_handoff_write_missing_ts_is_400(client):
    phone = _seed_thread("+491704444442")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic"})
    assert r.status_code == 400


def test_handoff_write_unparseable_ts_is_400(client):
    phone = _seed_thread("+491704444443")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic", "ts": "not-a-date"})
    assert r.status_code == 400


def test_handoff_write_crm_candidate_id_not_validated(client):
    """crm_candidate_id is NOT validated against sales_brain -- any string is accepted."""
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"crm_candidate_id": "does-not-exist-in-crm", "clinic_id": "c1",
                          "status": "sent_to_clinic", "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 200
    assert r.json()["applied"] is True


# --- handoff write-back: idempotency + audit trail ----------------------------------------------------

def test_handoff_write_is_idempotent_on_same_event(client):
    phone = _seed_thread("+491705555550")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    payload = {"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
               "ts": "2026-09-30T10:00:00+00:00", "message_id": "m1"}
    r1 = client.post("/api/wa/pro/handoffs", headers=WH, json=payload)
    assert r1.json() == {"applied": True, "duplicate": False, "current": True,
                         "lead_key": f"thread:{tid}", "target_key": "clinic:c1", "status": "sent_to_clinic"}
    r2 = client.post("/api/wa/pro/handoffs", headers=WH, json=payload)
    assert r2.json()["applied"] is False
    assert r2.json()["duplicate"] is True
    events = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=RH).json()["events"]
    assert len(events) == 1   # the duplicate never appended a second event


def test_handoff_write_null_message_id_counts_as_a_value_for_dedup(client):
    """Two posts with no message_id at all for the same (lead,target,status) also dedup -- ifnull()
    folds both nulls together, not just literal matches (design decision 7)."""
    phone = _seed_thread("+491705555551")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    payload = {"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic", "ts": "2026-09-30T10:00:00+00:00"}
    client.post("/api/wa/pro/handoffs", headers=WH, json=payload)
    r2 = client.post("/api/wa/pro/handoffs", headers=WH, json=payload)
    assert r2.json()["duplicate"] is True


def test_handoff_write_records_prev_status_in_event(client):
    phone = _seed_thread("+491705555552")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "clinic_replied",
                     "ts": "2026-09-30T11:00:00+00:00"})
    events = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=RH).json()["events"]
    assert events[0]["prev_status"] is None
    assert events[1]["prev_status"] == "sent_to_clinic"


def test_handoff_write_out_of_order_event_is_audit_only(client):
    """An older event (by ts) arriving after a newer one is recorded but never becomes current --
    response names current:false, and a later read still shows the newer status (design decision 7)."""
    phone = _seed_thread("+491705555553")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "clinic_replied",
                     "ts": "2026-09-30T11:00:00+00:00"})
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                          "ts": "2026-09-30T09:00:00+00:00"})
    assert r.json()["applied"] is True
    assert r.json()["current"] is False
    rows = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=RH).json()["rows"]
    assert rows[0]["status"] == "clinic_replied"


def test_handoff_write_tie_goes_to_later_arrival(client):
    phone = _seed_thread("+491705555554")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    same_ts = "2026-09-30T10:00:00+00:00"
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic", "ts": same_ts})
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "clinic_id": "c1", "status": "clinic_replied", "ts": same_ts})
    assert r.json()["current"] is True
    rows = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=RH).json()["rows"]
    assert rows[0]["status"] == "clinic_replied"


def test_handoff_no_uniqueness_on_message_id_alone_several_clinics(client):
    """One letter/message_id can cover several clinics -- both rows must be written."""
    phone = _seed_thread("+491705555555")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    common_msg = "letter-batch-42"
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00", "message_id": common_msg})
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "clinic_id": "c2", "status": "sent_to_clinic",
                          "ts": "2026-09-30T10:00:00+00:00", "message_id": common_msg})
    assert r.json()["applied"] is True
    rows = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=RH).json()["rows"]
    assert {row["target_key"] for row in rows} == {"clinic:c1", "clinic:c2"}


def test_handoff_target_key_variants(client):
    phone = _seed_thread("+491705555556")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    r1 = client.post("/api/wa/pro/handoffs", headers=WH,
                     json={"thread_id": tid, "clinic_id": "c9", "status": "sent_to_clinic",
                           "ts": "2026-09-30T10:00:00+00:00"})
    assert r1.json()["target_key"] == "clinic:c9"
    r2 = client.post("/api/wa/pro/handoffs", headers=WH,
                     json={"thread_id": tid, "clinic_name": "Klinikum Extern", "external_ref": "ref-77",
                           "status": "sent_to_clinic", "ts": "2026-09-30T10:00:00+00:00"})
    assert r2.json()["target_key"] == "ext:ref-77"
    r3 = client.post("/api/wa/pro/handoffs", headers=WH,
                     json={"thread_id": tid, "clinic_name": "  Klinikum   Unbekannt  ",
                           "status": "sent_to_clinic", "ts": "2026-09-30T10:00:00+00:00"})
    assert r3.json()["target_key"] == "name:klinikum unbekannt"


# --- thread-level handoff.status mapping ---------------------------------------------------------

def test_thread_handoff_none_when_not_consented(client):
    phone = _seed_thread("+491706666660")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"] is None


def _consent(phone):
    qconn = Q.db()
    qconn.execute("insert into wa_queue_candidates (phone, consented_at, profile_json, status) values (?,?,?,?)",
                 (phone, "2026-09-30T08:00:00+00:00", json.dumps({"region": "Oberbayern"}), "queued"))
    qconn.commit()
    qconn.close()


def test_thread_handoff_status_transitions(client):
    phone = "+491706666661"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)

    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["status"] == "queued"

    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["status"] == "in_progress"

    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "interview_scheduled",
                     "ts": "2026-09-30T11:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["status"] == "attention"
    assert row["handoff"]["clinics"][0]["attention"] is True

    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "contract_signed",
                     "ts": "2026-09-30T12:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["status"] == "signed"
    assert row["handoff"]["clinics"][0]["attention"] is False


def test_thread_handoff_closed_when_every_target_declined_or_closed(client):
    phone = "+491706666662"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "declined",
                     "ts": "2026-09-30T10:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["status"] == "closed"


def test_thread_handoff_followup_sent_never_clears_attention(client):
    phone = "+491706666663"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "clinic_replied",
                     "ts": "2026-09-30T10:00:00+00:00"})
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "followup_sent",
                     "ts": "2026-09-30T11:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["status"] == "attention"


# --- leads endpoint (Daria, TASK-396) --------------------------------------------------------------

def _sales_brain_fixture(path, phone_e164, candidate_id="cand-1", ambiguous=False):
    """A synthetic sales_brain sqlite -- never the real /opt/clinic-dispatcher file -- with just
    enough of candidate_whatsapp_messages / candidate_clinic_cases / placement_case_state to exercise
    _crm_match / _crm_cases, using the column names confirmed against the real schema."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        create table candidate_whatsapp_messages (id integer primary key, candidate_id text, phone_e164 text);
        create table candidate_clinic_cases (id integer primary key, workspace_id text, candidate_id text,
            company_id text, clinic_key text, status text, register_signed integer, contract_start_date text,
            primary_contact_email text, metadata_json text, created_at text, updated_at text);
        create table placement_case_state (case_id integer primary key, placement_stage text, waiting_for text,
            next_action text, due_at text, scheduled_event_at text);
    """)
    conn.execute("insert into candidate_whatsapp_messages (candidate_id, phone_e164) values (?,?)",
                (candidate_id, phone_e164))
    if ambiguous:
        conn.execute("insert into candidate_whatsapp_messages (candidate_id, phone_e164) values (?,?)",
                    ("cand-2", phone_e164))
    conn.execute("insert into candidate_clinic_cases (id, workspace_id, candidate_id, company_id, clinic_key, "
                "status, created_at, updated_at) values (1, 'ws1', ?, 'co1', 'ck1', 'open', 'x', 'y')",
                (candidate_id,))
    conn.execute("insert into placement_case_state (case_id, placement_stage) values (1, 'contacted')")
    conn.commit()
    conn.close()


def test_leads_missing_sales_brain_is_unavailable_not_an_exception(client):
    """No synthetic file exists at WA_SALES_BRAIN_PATH in the base `env` fixture -- loud, not a 500."""
    phone = "+491707777770"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    r = client.get("/api/wa/pro/leads", headers=RH)
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["crm_match"] == "unavailable"
    assert body["rows"][0]["crm_candidate_id"] is None
    assert any("sales_brain.sqlite not found" in g for g in body["gaps"])


def test_leads_never_opens_the_real_sales_brain_path_by_accident(env):
    """Guards the exact mistake made during manual verification of this feature: WA_SALES_BRAIN_PATH
    must be pointed at a synthetic/missing file for every test, never left at the real default."""
    assert C.sales_brain_path() != "/opt/clinic-dispatcher/var/sales_brain.sqlite"


def test_leads_unambiguous_crm_match(client, monkeypatch, tmp_path):
    phone = "+491707777771"
    sb_path = tmp_path / "synthetic_sales_brain.sqlite"
    _sales_brain_fixture(sb_path, phone, candidate_id="cand-42")
    monkeypatch.setenv("WA_SALES_BRAIN_PATH", str(sb_path))
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    body = client.get("/api/wa/pro/leads", headers=RH).json()
    row = body["rows"][0]
    assert row["crm_candidate_id"] == "cand-42"
    assert row["crm_match"] is None
    assert len(row["crm_cases"]) == 1
    assert row["crm_cases"][0]["placement_stage"] == "contacted"
    assert row["crm_freshness"] is not None


def test_leads_ambiguous_crm_match(client, monkeypatch, tmp_path):
    phone = "+491707777772"
    sb_path = tmp_path / "synthetic_sales_brain_ambiguous.sqlite"
    _sales_brain_fixture(sb_path, phone, candidate_id="cand-a", ambiguous=True)
    monkeypatch.setenv("WA_SALES_BRAIN_PATH", str(sb_path))
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    body = client.get("/api/wa/pro/leads", headers=RH).json()
    row = body["rows"][0]
    assert row["crm_candidate_id"] is None
    assert row["crm_match"] == "ambiguous"
    assert row["crm_cases"] == []


def test_leads_matched_clinics_and_handoffs(client):
    phone = "+491707777773"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    qconn = Q.db()
    qconn.execute("insert into wa_queue_matches (phone, clinic_id, posting_id, score, reasons_json) "
                 "values (?,?,?,?,?)", (phone, "c1", 1, 77, "[]"))
    qconn.commit()
    qconn.close()
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})
    body = client.get("/api/wa/pro/leads", headers=RH).json()
    row = body["rows"][0]
    assert row["matched_clinics"] == [{"clinic_id": "c1", "clinic_name": "Klinikum Test",
                                       "town": "Testort", "score": 77}]
    assert row["handoffs"][0]["status"] == "sent_to_clinic"


def test_leads_base_gaps_always_present(client):
    body = client.get("/api/wa/pro/leads", headers=RH).json()
    assert len(body["gaps"]) >= len(PA.BASE_GAPS)
