"""Tests for app/wa/pro_api.py (TASK-395/396, Ivan 2026-09-29/30): the bearer-token Pro read API and
Daria's handoff write-back. All offline -- one tmp sqlite per test (app/wa/config.py:SQLITE_PATH
monkeypatched, the same pattern tests/test_wa_queue.py already uses), a synthetic app.data snapshot,
and WA_SALES_BRAIN_PATH always pointed at a synthetic tmp file or a deliberately-missing path --
never the colleague's real /opt/clinic-dispatcher/var/sales_brain.sqlite.
"""
import base64
import json
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import data as D
from app.wa import asgi
from app.wa import config as C
from app.wa import meta as META
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
    """``with`` is load-bearing (Starlette only runs lifespan/``on_event`` handlers inside the
    context manager): asgi.py's startup hook is what creates the schema before any Pro API GET route
    opens its read-only connection (review item 7) -- without it, every test here would just be
    proving db_ro() fails on a file that was never created, same as production would without the
    hook running under uvicorn."""
    with TestClient(asgi.app) as c:
        yield c


CAMPAIGN_TEMPLATE = {"name": "test_campaign", "language": "de", "parameter_format": "POSITIONAL",
                     "components": [{"type": "BODY", "text": "Guten Tag {{1}}, neue Stellen fuer Sie.",
                                     "example": {"body_text": [["Frau X"]]}}]}
CAMPAIGN_PARAMS = {"body": ["Frau Test"]}


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


def test_write_route_403_with_read_token(client):
    """A read token can never write -- 403 (recognised, wrong scope), not 401 (review item 12)."""
    _seed_thread()
    r = client.post("/api/wa/pro/handoffs", headers=RH,
                    json={"thread_id": "whatever", "clinic_id": "c1", "status": "sent_to_clinic",
                          "ts": "2026-09-30T10:00:00+00:00"})
    assert r.status_code == 403


def test_leads_403_with_read_token(client):
    """review item 12: the board's read token can read threads/detail/messages/health only --
    /leads is Daria-scope exclusively."""
    assert client.get("/api/wa/pro/leads", headers=RH).status_code == 403


def test_handoffs_get_403_with_read_token(client):
    assert client.get("/api/wa/pro/handoffs?thread_id=t_whatever", headers=RH).status_code == 403


def test_daria_scope_routes_503_when_write_token_unset(tmp_path, monkeypatch):
    """Unset env is 503 for every route that token would serve, regardless of a valid read token
    being sent alongside it (review item 12)."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setenv("WA_API_TOKEN", READ_TOKEN)
    monkeypatch.delenv("WA_API_WRITE_TOKEN", raising=False)
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    test_client = TestClient(asgi.app)
    assert test_client.get("/api/wa/pro/leads", headers=RH).status_code == 503
    assert test_client.get("/api/wa/pro/handoffs?thread_id=t_x", headers=RH).status_code == 503


def test_non_ascii_bearer_token_is_401_not_500(client):
    """hmac.compare_digest raises TypeError on a non-ASCII str; comparing UTF-8 bytes instead means
    it simply never matches (review item 12's LOW note). The header value is sent as raw UTF-8 bytes
    on purpose: httpx's own header normalizer only accepts a plain ``str`` when it is pure ASCII, so
    a non-ASCII token can only ever reach the server already encoded -- exactly how a real client
    (or a hand-rolled curl -H) would have to send one too."""
    r = client.get("/api/wa/pro/threads",
                   headers={"Authorization": "Bearer café-token".encode("utf-8")})
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


def _five_digit_windows(digits):
    """Every 5-digit substring of a raw phone -- checking all of them catches any leaked run of 5
    or more (a longer leak still contains at least one 5-digit window), without flagging unrelated
    numbers elsewhere in a response (a Meta error code, a row id, a year) the way a blanket 'no digit
    run over 4 anywhere in the text' scan would."""
    return {digits[i:i + 5] for i in range(len(digits) - 4)}


def test_raw_phone_never_appears_in_any_pro_response(client):
    """Review item 11: the old version of this test only posted one handoff and grepped the combined
    text for the one raw phone it had seeded -- it would have kept passing even if every field this
    seeds below leaked, because none of them were exercised. wamid.* base64-encodes the raw phone
    (the actual root cause, not an abstract worry), so this seeds one into every field known to have
    carried one in the stored data: a failed-delivery status (store.delivery_failure_text embeds the
    wamid literally), a campaign send (card.campaign's own stored dict carries {..., wamid}), an
    inbound message's forbidden meta key (context.id/context.from -- outside ALLOWED_META_KEYS, so
    _filter_meta must drop the whole key, not just scrub inside it), and both consent-recovery
    storage shapes (a genuine Meta-rail tap and a bridge-rail typed-reply recovery, each anchored to
    its own offer wamid). Every route's full JSON is then checked for the 'wamid' substring and for
    any 5-digit run of either seeded phone's own digits (phone_masked's contract is exactly the
    calling code (<=2 digits) plus the last 4 -- never a longer run, app/wa/phones.py)."""
    phone = _seed_thread("+491709998877")
    phone2 = "+491709998878"
    raw_digits, raw_digits2 = phone.lstrip("+"), phone2.lstrip("+")
    b64_1 = base64.b64encode(raw_digits.encode()).decode()
    b64_2 = base64.b64encode(raw_digits2.encode()).decode()

    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
        offer_wamid = f"wamid.HBgL{b64_1}FQIAEhgEQKAAoffer=="
        campaign_wamid = f"wamid.HBgL{b64_1}FQIAEhgEQKAAcamp=="
        failed_wamid = f"wamid.HBgL{b64_1}FQIAEhgEQKAAfail=="
        tap_wamid = f"wamid.HBgL{b64_1}FQIAEhgEQKAAtap1=="
        rendered = META.render_template(CAMPAIGN_TEMPLATE, CAMPAIGN_PARAMS)
        ST.record_campaign_send(c, phone, campaign_wamid, rendered, "camp-raw-phone-test")
        ST.record_message_status(c, phone, {"id": failed_wamid, "status": "failed",
                                            "timestamp": "1234567890",
                                            "errors": [{"code": 131026, "title": "not on WhatsApp"}]})
        ST.record_inbound(c, phone, "wamid.forbidden-meta-key", "hi", kind="text",
                          meta={"context": {"id": offer_wamid, "from": raw_digits}})
        # Consent recovery, shape 1: a genuine Meta-rail button tap.
        ST.record_outbound(c, phone, offer_wamid, "Duerfen wir Ihr Profil anonymisiert teilen?",
                           kind="buttons", meta={"action": "consent_offer",
                                                 "buttons": [{"id": "consent:yes", "title": "Ja"}]})
        ST.record_inbound(c, phone, tap_wamid, "Ja", kind="interactive",
                          meta={"button_id": "consent:yes"})
        # Consent recovery, shape 2: a bridge-rail typed-reply recovery, on a second phone.
        ST.thread(c, phone2)
        offer_wamid2 = f"wamid.HBgL{b64_2}FQIAEhgEQKAAoffer2=="
        tap_wamid2 = f"wamid.HBgL{b64_2}FQIAEhgEQKAAtap2=="
        ST.record_outbound(c, phone2, offer_wamid2, "1) Ja  2) Nein", kind="buttons",
                           meta={"buttons": [{"id": "consent:yes", "title": "Ja"}]})
        ST.record_inbound(c, phone2, tap_wamid2, "1", kind="text", meta={})
        c.execute("update wa_messages set meta=? where wamid=?",
                 (json.dumps({"button_recovery": {"tier": "ordinal", "button_id": "consent:yes",
                                                  "offer_wamid": offer_wamid2, "token": "1"}}),
                  tap_wamid2))
        c.commit()
        tid2 = ST.thread_id_for_phone(c, phone2)

    for p, cand_phone in ((phone, phone), (phone2, phone2)):
        qconn = Q.db()
        qconn.execute("insert into wa_queue_candidates (phone, consented_at, profile_json, status) "
                      "values (?,?,?,?)",
                     (cand_phone, "2026-09-30T08:05:00+00:00", json.dumps({}), "queued"))
        qconn.commit()
        qconn.close()

    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})

    bodies = []
    for tid_ in (tid, tid2):
        for path in ("/api/wa/pro/threads", f"/api/wa/pro/threads/{tid_}",
                     f"/api/wa/pro/threads/{tid_}/messages", "/api/wa/pro/health",
                     f"/api/wa/pro/handoffs?thread_id={tid_}", "/api/wa/pro/leads"):
            bodies.append(client.get(path, headers=WH).text)
    full = "\n".join(bodies)

    # _scrub_wamids redacts to the literal placeholder "wamid.…" on purpose (a marker that a
    # wamid was there and was removed) -- so the check is that none of the actual seeded wamids (or
    # their base64 phone payloads) survive verbatim, not that the substring "wamid" is gone outright.
    for wamid in (offer_wamid, campaign_wamid, failed_wamid, tap_wamid, offer_wamid2, tap_wamid2,
                  "wamid.forbidden-meta-key"):
        assert wamid not in full, f"an unredacted wamid leaked into a Pro API response: {wamid!r}"
    assert b64_1 not in full, "a wamid's base64 phone payload leaked unredacted"
    assert b64_2 not in full, "a wamid's base64 phone payload leaked unredacted"
    for digits in (raw_digits, raw_digits2):
        for window in _five_digit_windows(digits):
            assert window not in full, f"a 5-digit run of a raw phone leaked: {window!r}"


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
    # Review item 13: synced_at/synced_source are additive (null when no engine has ever written a
    # wa_rail_sync row) -- not part of this envelope's shape before this fix pass.
    assert set(body) == {"total", "limit", "offset", "next_offset", "test_threads", "generated_at",
                         "source", "rows", "synced_at", "synced_source"}
    assert body["synced_at"] is None
    assert body["synced_source"] is None


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


def test_card_campaign_is_the_campaign_id_string_not_the_stored_dict(client):
    """Review item 3: store.record_campaign_send's card.campaign is {campaign_id, template_name,
    language, rendered_text, buttons, sent_at, wamid} -- the response must be the campaign_id string
    alone (the frontend renders "Campaign " + r.card.campaign; the stored dict also carries a
    wamid, which must never reach a response at all -- see the wamid-scrub tests)."""
    phone = "+491701111114"
    rendered = META.render_template(CAMPAIGN_TEMPLATE, CAMPAIGN_PARAMS)
    with ST.db() as c:
        ST.record_campaign_send(c, phone, "wamid.campaign-leak-token", rendered, "bayern-2026-09")
        tid = ST.thread_id_for_phone(c, phone)
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["card"]["campaign"] == "bayern-2026-09"
    row2 = client.get("/api/wa/pro/threads", headers=RH).json()["rows"][0]
    assert row2["card"]["campaign"] == "bayern-2026-09"


def test_thread_card_carries_housing_flexible_and_anonymous_send_offered(client):
    """docs/wa-dashboard.md's board-scope card contract (Ivan, 2026-10-01) names
    housing_flexible/anonymous_send_offered on CardSummary itself, not only on the Daria-scope
    /leads route's LeadCardValues (see test_leads_card_values_come_from_the_live_card_not_the_
    consent_snapshot below) -- both routes read the same live card (t["slots"])."""
    phone = "+491701111115"
    with ST.db() as c:
        t = ST.thread(c, phone)
        t["slots"].update(housing_flexible=True, anonymous_send_offered=True)
        ST.save_thread(c, t)
        tid = ST.thread_id_for_phone(c, phone)
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["card"]["housing_flexible"] is True
    assert row["card"]["anonymous_send_offered"] is True
    row2 = client.get("/api/wa/pro/threads", headers=RH).json()["rows"][0]
    assert row2["card"]["housing_flexible"] is True
    assert row2["card"]["anonymous_send_offered"] is True


def test_thread_id_for_phone_is_minted_by_the_engine_not_by_a_lazy_read(client):
    """Review item 7: thread_id_for_phone is a plain, fail-loud read -- minting happens wherever a
    wa_threads row is first created (thread/pin_rail/record_campaign_send), in the SAME transaction,
    never lazily on a Pro API read."""
    phone = "+491701111112"
    with ST.db() as c:
        # thread() alone (no Pro API call in between) must already have minted the id.
        ST.thread(c, phone)
        tid = ST.thread_id_for_phone(c, phone)
    assert tid.startswith("t_")
    row = client.get("/api/wa/pro/threads", headers=RH).json()["rows"][0]
    assert row["thread_id"] == tid


def test_thread_id_for_phone_raises_for_a_phone_with_no_thread():
    with ST.db() as c:
        with pytest.raises(RuntimeError):
            ST.thread_id_for_phone(c, "+49170000000000")


def test_pro_read_never_blocks_behind_st_lock(client):
    """Review item 7: a GET route must never take ST._lock -- it uses its own read-only connection
    (db_ro). Holds the lock on another thread for up to 5s and proves the read answers almost
    immediately WHILE the lock is still held (not merely that it eventually answers once the holder
    gives up) -- a route that still took the lock would block for the holder's full wait."""
    _seed_thread("+491701111113")
    release = threading.Event()
    holder_ready = threading.Event()

    def _hold_lock():
        with ST._lock:
            holder_ready.set()
            release.wait(timeout=5)

    t = threading.Thread(target=_hold_lock, daemon=True)
    t.start()
    try:
        assert holder_ready.wait(timeout=5)
        started = time.monotonic()
        r = client.get("/api/wa/pro/threads", headers=RH)
        elapsed = time.monotonic() - started
        assert r.status_code == 200
        assert not release.is_set()   # the lock was still held throughout the request
        assert elapsed < 2.0          # vs. the holder's up-to-5s wait -- this did not queue behind it
    finally:
        release.set()
        t.join(timeout=5)


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


def test_messages_meta_is_whitelisted(client):
    """Review item 2: only buttons/scope_refusal/action/template/transcript survive -- everything
    else a stored row's meta can carry (reply_to_wamid, context, button_recovery, media_id/filename,
    contacts/location, ...) is dropped outright, not merely scrubbed."""
    phone = "+491702222227"
    with ST.db() as c:
        ST.thread(c, phone)
        ST.record_inbound(c, phone, "wamid-meta-1", "danke fuer die nachricht", kind="text",
                          meta={"reply_to_wamid": "wamid.quoted.secret", "context": {"from": phone},
                                "action": "reply", "media_id": "media-secret", "media_filename": "lebenslauf.pdf",
                                "contacts": [{"name": "x"}]})
        ST.record_outbound(c, phone, "wamid-meta-2", "Bitte waehlen", kind="buttons",
                           meta={"action": "offer", "buttons": [{"id": "consent:yes", "title": "Ja"}],
                                 "scope_refusal": None})
        tid = ST.thread_id_for_phone(c, phone)
    rows = client.get(f"/api/wa/pro/threads/{tid}/messages", headers=RH).json()["rows"]
    in_row = next(r for r in rows if r["direction"] == "in")
    out_row = next(r for r in rows if r["direction"] == "out")
    assert set(in_row["meta"]) == {"action"}
    assert "reply_to_wamid" not in in_row["meta"]
    assert "context" not in in_row["meta"]
    assert "media_id" not in in_row["meta"]
    assert "media_filename" not in in_row["meta"]
    assert "contacts" not in in_row["meta"]
    assert set(out_row["meta"]) == {"action", "buttons", "scope_refusal"}


def test_messages_unknown_delivery_status_passes_through_raw(client):
    """Review item 9: DeliveryStatus is a plain str -- an unknown Meta status must not 500 the whole
    thread's messages endpoint."""
    phone = "+491702222228"
    with ST.db() as c:
        ST.thread(c, phone)
        ST.record_outbound(c, phone, "wamid-status-1", "Hallo", kind="text")
        ST.record_message_status(c, phone, {"id": "wamid-status-1", "status": "a_future_meta_status",
                                            "timestamp": "1700000000"})
        tid = ST.thread_id_for_phone(c, phone)
    r = client.get(f"/api/wa/pro/threads/{tid}/messages", headers=RH)
    assert r.status_code == 200
    row = next(x for x in r.json()["rows"] if x["direction"] == "out")
    assert row["status"] == "a_future_meta_status"


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


def test_health_never_leaks_infra_paths_hosts_or_ids(client):
    """pflege-fe review (2026-10-01): luna_media_dir (a local home path), luna_media_host (a
    hostname), graph_api_version and bridge_phone_number_id reached the board/browser through this
    route and sat in a public-repo fixture. Dropped outright, not merely left undeclared on
    HealthResponse -- that model is extra="allow" (readiness()'s own key set varies with WA_BRAIN),
    so an undeclared field would otherwise still pass straight through unchanged. The rest of the
    payload is scanned the same way, so a future field cannot reintroduce the same leak unnoticed."""
    body = client.get("/api/wa/pro/health", headers=RH).json()
    for key in ("graph_api_version", "bridge_phone_number_id", "luna_media_host", "luna_media_dir"):
        assert key not in body, f"{key} must never reach the Pro API"
    for key, value in body.items():
        if not isinstance(value, str):
            continue
        low = value.lower()
        assert "/" not in value, f"{key}={value!r} looks like a path"
        assert "home" not in low, f"{key}={value!r} looks like a path"
        assert "macmini" not in low, f"{key}={value!r} looks like a hostname"
        assert "cursorworker" not in low, f"{key}={value!r} looks like a username"


# --- escalated_at: stamped once, never overwritten --------------------------------------------------

def test_escalated_at_stamped_once_and_never_overwritten(monkeypatch):
    """Review item 11: ST.now_iso() truncates to whole seconds, so the original test's
    ``time.sleep(0.01)`` passed even when the stamp WAS overwritten, as long as both calls landed in
    the same wall-clock second (which a 10ms sleep practically always does) -- it was testing nothing.
    A monkeypatched clock that returns two DISTINCT values makes an overwrite detectable regardless
    of real elapsed time."""
    stamps = iter(["2026-09-30T10:00:00+00:00", "2026-09-30T10:05:00+00:00"])
    monkeypatch.setattr(ST, "now_iso", lambda: next(stamps))
    card = {}
    ESC.record_escalation(card, "explicit_human_request", "wants a human")
    first_stamp = card["_escalated_at"]
    assert first_stamp == "2026-09-30T10:00:00+00:00"
    ESC.record_escalation(card, "pet_policy_question", "second reason")
    assert card["_escalated_at"] == first_stamp   # not the second clock value


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
    events = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=WH).json()["events"]
    assert len(events) == 1   # the duplicate never appended a second event


def test_handoff_write_numeric_message_id_reposted_is_a_noop_duplicate_not_500(client):
    """Review item 8: a numeric message_id (e.g. Daria's own system sending an int) re-posted used
    to 500 (IntegrityError on the dedup index, which compares ifnull() text expressions) because the
    SQLite storage class of the id differed between the two calls. HandoffWriteRequest coerces it to
    str before anything reaches the database."""
    phone = _seed_thread("+491705555557")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    payload = {"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
               "ts": "2026-09-30T10:00:00+00:00", "message_id": 555}
    r1 = client.post("/api/wa/pro/handoffs", headers=WH, json=payload)
    assert r1.status_code == 200
    assert r1.json()["applied"] is True
    r2 = client.post("/api/wa/pro/handoffs", headers=WH, json=payload)
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True


def test_handoff_write_bad_note_type_is_400_not_500(client):
    phone = _seed_thread("+491705555558")
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    r = client.post("/api/wa/pro/handoffs", headers=WH,
                    json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                          "ts": "2026-09-30T10:00:00+00:00", "note": {"not": "a string"}})
    assert r.status_code == 400


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
    events = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=WH).json()["events"]
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
    rows = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=WH).json()["rows"]
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
    rows = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=WH).json()["rows"]
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
    rows = client.get(f"/api/wa/pro/handoffs?thread_id={tid}", headers=WH).json()["rows"]
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
    assert row["handoff"]["clinics"] == 0   # review item 5: an INTEGER count (no wa_queue_matches seeded here)
    assert row["handoff"]["targets"][0]["attention"] is True

    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "contract_signed",
                     "ts": "2026-09-30T12:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["status"] == "signed"
    assert row["handoff"]["targets"][0]["attention"] is False


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


def test_thread_handoff_clinics_is_the_matched_count_not_the_target_list(client):
    """Review item 5: clinics is the INTEGER count of matched clinics (wa_queue_matches), which can
    differ from the number of per-clinic handoff rows (targets) -- the contract and the already-built
    frontend both read clinics as a count, never a list."""
    phone = "+491706666664"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    qconn = Q.db()
    qconn.execute("insert into wa_queue_matches (phone, clinic_id, posting_id, score, reasons_json) "
                 "values (?,?,?,?,?)", (phone, "c1", 1, 90, "[]"))
    qconn.execute("insert into wa_queue_matches (phone, clinic_id, posting_id, score, reasons_json) "
                 "values (?,?,?,?,?)", (phone, "c2", 1, 80, "[]"))
    qconn.commit()
    qconn.close()
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["clinics"] == 2            # matched pool size (c1 + c2)
    assert len(row["handoff"]["targets"]) == 1        # only c1 has a handoff row so far
    assert row["handoff"]["targets"][0]["clinic_id"] == "c1"


# --- targets[].clinic_name: filled from the live registry when the write-back omits it --------------

def test_handoff_target_clinic_name_filled_from_the_registry_when_write_back_omits_it(client):
    """pflege-fe review (2026-10-01): a write-back carrying only clinic_id must not leave
    targets[].clinic_name null when the live board registry (app.data, the same D.clinic() lookup
    _matched_clinics already joins on above) knows this clinic's name."""
    phone = "+491706666665"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["targets"][0]["clinic_name"] == "Klinikum Test"


def test_handoff_target_clinic_name_from_the_write_back_wins_over_the_registry(client):
    """The write-back's own name always wins, even when it disagrees with the live registry (Daria
    may hold a name the board no longer carries)."""
    phone = "+491706666666"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c1", "clinic_name": "Klinikum Wie Daria Sie Kennt",
                     "status": "sent_to_clinic", "ts": "2026-09-30T10:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["targets"][0]["clinic_name"] == "Klinikum Wie Daria Sie Kennt"


def test_handoff_target_clinic_name_stays_null_when_neither_source_has_it(client):
    phone = "+491706666667"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c-unknown-to-the-board", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["handoff"]["targets"][0]["clinic_name"] is None


# --- ball: a stopped or suppressed thread never reads "us" or "them" --------------------------------

def test_thread_ball_is_silent_for_a_stopped_thread(client):
    """pflege-fe review (2026-10-01): a STOP thread showed ball "us" (the candidate's STOP is the
    last message, and it settles nothing in app.wa.luna.reporting.ball_for's own claim-state sense),
    labelling it "our turn" even though nobody may write to it again on any rail."""
    phone = "+491706666668"
    with ST.db() as c:
        ST.thread(c, phone)
        ST.record_inbound(c, phone, "wamid.stop.in", "Stopp")
        t = ST.thread(c, phone)
        t["stopped"], t["stopped_reason"] = True, ST.STOPPED
        ST.save_thread(c, t)
        tid = ST.thread_id_for_phone(c, phone)
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["ball"] == "silent" and row["stopped"] is True


def test_thread_ball_is_silent_for_a_suppressed_thread(client):
    """Suppressed via wa_suppressions (TASK-347) without wa_threads.stopped ever being set -- the
    cross-rail list is keyed on the human, not the thread, so this is the "stopped" column's own
    thread-scoped opt-out catching up, not a dupe of the test above."""
    from app.wa import suppression as SUP

    phone = "+491706666669"
    with ST.db() as c:
        ST.thread(c, phone)
        ST.record_inbound(c, phone, "wamid.supp.in", "Hallo")
        ST.record_outbound(c, phone, "wamid.supp.out", "Willkommen")
        tid = ST.thread_id_for_phone(c, phone)
        SUP.suppress(c, phone, SUP.REASON_STOP, "meta", trigger_text="Stopp (another rail)")
    row = client.get(f"/api/wa/pro/threads/{tid}", headers=RH).json()["thread"]
    assert row["ball"] == "silent" and row["stopped"] is False
    assert row["suppression"] is not None


# --- leads endpoint (Daria, TASK-396) --------------------------------------------------------------

def _sales_brain_fixture(path, phone_e164, candidate_id=1, ambiguous=False):
    """A synthetic sales_brain sqlite -- never the real /opt/clinic-dispatcher file -- with just
    enough of candidate_whatsapp_messages / candidate_clinic_cases / placement_case_state to exercise
    _crm_match / _crm_cases, using the REAL column types (review item 4, 2026-09-30: confirmed via a
    read-only ``pragma table_info`` against the real file -- candidate_id is INTEGER everywhere, never
    rows). The original fixture declared ``candidate_id text`` and passed string ids like "cand-42",
    which is why /leads's 500 on a clear CRM match was invisible to this suite."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        create table candidate_whatsapp_messages (id integer primary key, candidate_id integer, phone_e164 text);
        create table candidate_clinic_cases (id integer primary key, workspace_id text, candidate_id integer,
            company_id text, clinic_key text, status text, register_signed integer, contract_start_date text,
            primary_contact_email text, metadata_json text, created_at text, updated_at text);
        create table placement_case_state (case_id integer primary key, placement_stage text, waiting_for text,
            next_action text, due_at text, scheduled_event_at text);
    """)
    conn.execute("insert into candidate_whatsapp_messages (candidate_id, phone_e164) values (?,?)",
                (candidate_id, phone_e164))
    if ambiguous:
        conn.execute("insert into candidate_whatsapp_messages (candidate_id, phone_e164) values (?,?)",
                    (candidate_id + 1000, phone_e164))
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
    r = client.get("/api/wa/pro/leads", headers=WH)
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
    """Review item 4: sales_brain's candidate_id is INTEGER; the response field is str -- the route
    must cast it, or FastAPI's own response validation 500s this (the original bug, invisible to the
    old text-typed fixture)."""
    phone = "+491707777771"
    sb_path = tmp_path / "synthetic_sales_brain.sqlite"
    _sales_brain_fixture(sb_path, phone, candidate_id=42)
    monkeypatch.setenv("WA_SALES_BRAIN_PATH", str(sb_path))
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    r = client.get("/api/wa/pro/leads", headers=WH)
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["crm_candidate_id"] == "42"
    assert isinstance(row["crm_candidate_id"], str)
    assert row["crm_match"] is None
    assert len(row["crm_cases"]) == 1
    assert row["crm_cases"][0]["placement_stage"] == "contacted"
    assert row["crm_freshness"] is not None


def test_leads_ambiguous_crm_match(client, monkeypatch, tmp_path):
    phone = "+491707777772"
    sb_path = tmp_path / "synthetic_sales_brain_ambiguous.sqlite"
    _sales_brain_fixture(sb_path, phone, candidate_id=43, ambiguous=True)
    monkeypatch.setenv("WA_SALES_BRAIN_PATH", str(sb_path))
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    body = client.get("/api/wa/pro/leads", headers=WH).json()
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
    body = client.get("/api/wa/pro/leads", headers=WH).json()
    row = body["rows"][0]
    assert row["matched_clinics"] == [{"clinic_id": "c1", "clinic_name": "Klinikum Test",
                                       "town": "Testort", "score": 77}]
    assert row["handoffs"][0]["status"] == "sent_to_clinic"


def test_leads_handoffs_clinic_name_also_filled_from_the_registry(client):
    """Same _clinic_statuses() backfill as the thread-detail targets tests above -- LeadRow.handoffs
    shares that same function, so the fix is not thread-detail-only."""
    phone = "+491707777775"
    with ST.db() as c:
        ST.thread(c, phone)
    _consent(phone)
    with ST.db() as c:
        tid = ST.thread_id_for_phone(c, phone)
    client.post("/api/wa/pro/handoffs", headers=WH,
               json={"thread_id": tid, "clinic_id": "c2", "status": "sent_to_clinic",
                     "ts": "2026-09-30T10:00:00+00:00"})
    body = client.get("/api/wa/pro/leads", headers=WH).json()
    row = body["rows"][0]
    assert row["handoffs"][0]["clinic_name"] == "Klinikum Zwei"


def test_leads_card_values_come_from_the_live_card_not_the_consent_snapshot(client):
    """Review item 10: region/city/department/housing_flexible/anonymous_send_offered come from the
    LIVE card (thread slots), never queue.card_to_candidate's consent-time snapshot -- which used a
    Regierungsbezirk for region and profile_json's departments[0] (not department_pref) for
    department, and has no housing_flexible/anonymous_send_offered at all. The snapshot below
    deliberately disagrees with the live card on every one of these fields, to prove which one wins."""
    phone = "+491707777774"
    with ST.db() as c:
        t = ST.thread(c, phone)
        t["slots"].update(region="Schwaben", city="Augsburg", department_pref="Intensivstation",
                          qualification_path="urkunde", housing_needed=True, housing_flexible=True,
                          anonymous_send_offered=True, people_count=2)
        ST.save_thread(c, t)
    qconn = Q.db()
    qconn.execute("insert into wa_queue_candidates (phone, consented_at, profile_json, status) values (?,?,?,?)",
                 (phone, "2026-09-30T08:00:00+00:00",
                  json.dumps({"region": "Schwaben (Regierungsbezirk)", "city": "Stale-Stadt",
                              "departments": ["Stale Department"], "needs_housing": False,
                              "people_count": 99, "german_level": "B2"}), "queued"))
    qconn.commit()
    qconn.close()
    row = client.get("/api/wa/pro/leads", headers=WH).json()["rows"][0]
    assert row["card"]["region"] == "Schwaben"
    assert row["card"]["city"] == "Augsburg"
    assert row["card"]["department"] == "Intensivstation"
    assert row["card"]["housing_needed"] is True
    assert row["card"]["housing_flexible"] is True
    assert row["card"]["anonymous_send_offered"] is True
    assert row["card"]["people_count"] == 2
    assert "cv_profile" not in row
    assert "german_level" not in row["card"]


def test_leads_gaps_name_cv_profile_and_german_level_as_not_stored(client):
    body = client.get("/api/wa/pro/leads", headers=WH).json()
    assert any("cv profile" in g for g in body["gaps"])
    assert any("german level" in g for g in body["gaps"])


def _set_at(c, wamid, at):
    """Backdate one stored message's ``at`` -- record_inbound always stamps real now(), and these
    consent-recovery tests need the tap/offer to land at-or-before a fixed, past consented_at."""
    c.execute("update wa_messages set at=? where wamid=?", (at, wamid))
    c.commit()


def test_consent_info_recovers_a_genuine_meta_rail_tap(client):
    """Review item 6: a real Meta-rail button tap is stored with Meta's own kind (interactive),
    never 'buttons' (that is only ever an OUTBOUND kind) -- the original query never matched this in
    production. button_id lives directly on the inbound row's meta. Ids returned are wa_messages.id,
    never a wamid."""
    phone = "+491707777775"
    with ST.db() as c:
        ST.thread(c, phone)
        ST.record_outbound(c, phone, "wamid.offer-1", "Duerfen wir Ihr Profil anonymisiert teilen?",
                           kind="buttons", meta={"action": "consent_offer",
                                                 "buttons": [{"id": "consent:yes", "title": "Ja"},
                                                            {"id": "consent:no", "title": "Nein"}]})
        _set_at(c, "wamid.offer-1", "2026-09-30T08:00:00+00:00")
        ST.record_inbound(c, phone, "wamid.tap-1", "Ja", kind="interactive",
                          meta={"button_id": "consent:yes"})
        _set_at(c, "wamid.tap-1", "2026-09-30T08:01:00+00:00")
        tap_id = c.execute("select id from wa_messages where wamid=?", ("wamid.tap-1",)).fetchone()[0]
        offer_id = c.execute("select id from wa_messages where wamid=?", ("wamid.offer-1",)).fetchone()[0]
    qconn = Q.db()
    qconn.execute("insert into wa_queue_candidates (phone, consented_at, profile_json, status) values (?,?,?,?)",
                 (phone, "2026-09-30T08:05:00+00:00", json.dumps({}), "queued"))
    qconn.commit()
    qconn.close()
    row = client.get("/api/wa/pro/leads", headers=WH).json()["rows"][0]
    assert row["consent"]["answer_message_id"] == str(tap_id)
    assert row["consent"]["offer_message_id"] == str(offer_id)
    assert row["consent"]["offer_text"] == "Duerfen wir Ihr Profil anonymisiert teilen?"
    assert "wamid" not in row["consent"]["answer_message_id"]
    assert "wamid" not in row["consent"]["offer_message_id"]


def test_consent_info_recovers_a_bridge_rail_typed_reply(client):
    """Review item 6: a phone-rail candidate has no reply buttons, so a genuine tap is impossible --
    app/wa/luna/choices.py recovers it from a typed reply and stores it under meta.button_recovery
    (kind stays 'text'), already carrying its own offer_wamid ('the offer is read, not
    reconstructed'). This must resolve too, and still return wa_messages.id, not a wamid."""
    phone = "+491707777776"
    with ST.db() as c:
        ST.thread(c, phone)
        ST.record_outbound(c, phone, "wamid.offer-2", "1) Ja  2) Nein", kind="buttons",
                           meta={"action": "consent_offer",
                                 "buttons": [{"id": "consent:yes", "title": "Ja"},
                                            {"id": "consent:no", "title": "Nein"}]})
        _set_at(c, "wamid.offer-2", "2026-09-30T08:00:00+00:00")
        ST.record_inbound(c, phone, "wamid.tap-2", "1", kind="text", meta={})
        _set_at(c, "wamid.tap-2", "2026-09-30T08:01:00+00:00")
        c.execute("update wa_messages set meta=? where wamid=?",
                 (json.dumps({"button_recovery": {"tier": "ordinal", "button_id": "consent:yes",
                                                  "offer_wamid": "wamid.offer-2", "token": "1"}}),
                  "wamid.tap-2"))
        c.commit()
        tap_id = c.execute("select id from wa_messages where wamid=?", ("wamid.tap-2",)).fetchone()[0]
        offer_id = c.execute("select id from wa_messages where wamid=?", ("wamid.offer-2",)).fetchone()[0]
    qconn = Q.db()
    qconn.execute("insert into wa_queue_candidates (phone, consented_at, profile_json, status) values (?,?,?,?)",
                 (phone, "2026-09-30T08:05:00+00:00", json.dumps({}), "queued"))
    qconn.commit()
    qconn.close()
    row = client.get("/api/wa/pro/leads", headers=WH).json()["rows"][0]
    assert row["consent"]["answer_message_id"] == str(tap_id)
    assert row["consent"]["offer_message_id"] == str(offer_id)


def test_leads_base_gaps_always_present(client):
    body = client.get("/api/wa/pro/leads", headers=WH).json()
    assert len(body["gaps"]) >= len(PA.BASE_GAPS)
