"""Offline proof for the phone-rail executor (TASK-130). FakeDriver only -- no adb, no phone, no ssh.

Every test here asserts something a real candidate would feel: a duplicate message, a silent
non-delivery recorded as sent, a bubble at 07:53 in the morning. The driver itself is covered in
tests/test_bridge_adb.py and the inbound ids in tests/test_bridge_relay.py.
"""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bridge import driver as D
from bridge import envelope as EN
from bridge import errors as E
from bridge import executor as X
from bridge import governor as G
from bridge import inbound as I
from bridge import ledger as L
from bridge import media as MD
from bridge import server as S
from bridge import watcher as W

BERLIN = ZoneInfo("Europe/Berlin")
PHONE = "+491700000001"
OTHER = "+491700000002"
KEY = "wab.o.0000000000000000000000000000aaaa"
KEY2 = "wab.o.0000000000000000000000000000bbbb"


class Clock:
    def __init__(self, moment):
        self.now = moment

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        return self.now


def berlin(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN).astimezone(timezone.utc)


class Rig:
    """ledger + governor + fake phone + executor, on one tmp sqlite file."""

    def __init__(self, tmp_path, *, moment=None, per_number_cap=3, ticks=None):
        self.clock = Clock(moment or berlin(2026, 9, 23, 10, 0))  # a Wednesday, inside 9-20
        self.ledger = L.Ledger(tmp_path / "ledger.sqlite")
        self.driver = D.FakeDriver(ticks=ticks)
        self.governor = G.Governor(self.ledger, per_number_daily_cap=per_number_cap,
                                   rng=__import__("random").Random(7))
        self.executor = X.Executor(ledger=self.ledger, governor=self.governor, driver=self.driver,
                                   rail_number=None, clock=self.clock, tick_wait_sec=0.0,
                                   sleep=lambda _s: None, monotonic=_fake_monotonic())

    def send(self, *, key=KEY, phone=PHONE, body="Guten Tag", action="reply", **extra):
        payload = {"client_msg_id": key, "to": phone, "kind": "text", "body": body,
                   "trace": {"action": action}}
        payload.update(extra)
        return self.executor.send(payload)


def _fake_monotonic():
    """Monotonic that jumps past any deadline on the second call, so wait loops run exactly once."""
    ticks = iter([0.0] + [10_000.0] * 1000)
    return lambda: next(ticks)


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.ledger.close()


# --- the invariant: a tick or nothing --------------------------------------------------------------
def test_a_verified_tick_is_the_only_way_to_report_sent(rig):
    status, body = rig.send()
    assert status == 200
    assert body["state"] == "sent"
    assert body["verified"] == {"method": "delivery_tick", "tick": "Gesendet", "tick_state": "sent",
                                "bubble_clock": "09:15", "body_sha256": D.body_sha256("Guten Tag")}
    # There is no provider message id on this rail and the field says so out loud.
    assert body["provider_msg_id"] is None
    assert rig.driver.sent == ["Guten Tag"]
    assert rig.ledger.get(KEY).state == L.SENT


def test_their_unverified_sentinel_is_a_504_and_never_a_sent(tmp_path):
    """Their whatsapp.py returns Bubble(status='unverified') and their CLI stores it as sent.
    It fired on 2 of 23 live sends. Here it is the 504 the whole task exists for."""
    rig = Rig(tmp_path, ticks=[D.UNVERIFIED])
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send()
    assert (caught.value.code, caught.value.status_code, caught.value.retryable) == \
        ("send_unconfirmed", 504, False)
    assert rig.ledger.get(KEY).state == L.UNCONFIRMED
    assert rig.driver.shots == [f"{L.thread_tag(PHONE)}_driver_unverified"]
    rig.ledger.close()


def test_a_bubble_with_no_tick_drawn_is_uncertain_not_sent(tmp_path):
    """Their send-verify loop stops at the body match; status '' means the server has not acked."""
    rig = Rig(tmp_path, ticks=[""])
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send()
    assert caught.value.status_code == 504
    assert rig.ledger.get(KEY).state == L.UNCONFIRMED
    rig.ledger.close()


def test_a_tick_that_appears_late_is_a_confirmed_send(tmp_path):
    """The bubble lands with no tick drawn; the re-read inside the same lock shows 'Zugestellt'.

    Their send-verify loop returns as soon as the body matches, so this is the common case, not an
    exotic one: the tick is drawn a moment after the bubble.
    """
    rig = Rig(tmp_path, ticks=[""])

    def draw_the_tick(drv):
        drv.thread = [b if b.tick else D.BubbleView(b.direction, b.text, b.clock, "Zugestellt")
                      for b in drv.thread]
    rig.driver.read_hook = draw_the_tick
    status, body = rig.send()
    assert (status, body["verified"]["tick_state"]) == (200, "delivered")
    assert rig.ledger.get(KEY).state == L.SENT
    rig.ledger.close()


# --- idempotency: first body wins -------------------------------------------------------------------
def test_the_same_key_and_body_replays_and_sends_nothing(rig):
    first = rig.send()
    second = rig.send()  # same instant on purpose: a replay must not be refused by pacing either
    assert rig.driver.sent == ["Guten Tag"]
    assert second[1]["replayed"] is True and second[1]["body_mismatch"] is False
    assert second[1]["sent_at"] == first[1]["sent_at"]
    assert second[1]["verified"] == first[1]["verified"]


def test_a_hundred_replays_produce_one_message(rig):
    rig.send()
    for _ in range(100):
        assert rig.send()[1]["replayed"] is True
    assert rig.driver.sent == ["Guten Tag"]


def test_a_different_body_under_a_live_key_is_a_mismatch_and_sends_nothing(rig):
    rig.send(body="erste Fassung")
    status, body = rig.send(body="zweite Fassung")
    assert status == 200
    assert body["body_mismatch"] is True and body["replayed"] is True
    # The ORIGINAL body's outcome comes back, not the new one's.
    assert body["verified"]["body_sha256"] == D.body_sha256("erste Fassung")
    assert rig.driver.sent == ["erste Fassung"]
    assert rig.ledger.mismatch_count(KEY) == 1


def test_a_different_body_on_a_resendable_key_is_a_409_and_sends_nothing(rig):
    """No first-body result exists to replay, so the conflict is loud rather than quietly dropped."""
    rig.driver.fail_on_open = "conversation did not open"
    with pytest.raises(E.BridgeRefusal):
        rig.send(body="erste Fassung")
    assert rig.ledger.get(KEY).state == L.NOT_ATTEMPTED
    rig.driver.fail_on_open = None
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(body="zweite Fassung")
    assert (caught.value.code, caught.value.status_code) == ("idempotency_conflict", 409)
    assert rig.driver.sent == []


def test_a_two_bubble_turn_whose_second_bubble_fails_does_not_resend_the_first(rig):
    """Their code requeues the whole item; one key per bubble means bubble 1 is never retyped."""
    rig.send(key=KEY, body="Bubble eins")
    rig.clock.advance(10)
    rig.driver.fail_on_send = "send button not visible after typing"
    with pytest.raises(E.BridgeRefusal):
        rig.send(key=KEY2, body="Bubble zwei")
    assert rig.ledger.get(KEY2).state == L.UNCONFIRMED

    # Catch-up regenerates the same turn: the same deterministic keys come back.
    rig.clock.advance(10)
    rig.driver.fail_on_send = None
    assert rig.send(key=KEY, body="Bubble eins")[1]["replayed"] is True
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(key=KEY2, body="Bubble zwei")
    assert caught.value.status_code == 504  # uncertain, and uncertain is never auto-resent
    assert rig.driver.sent == ["Bubble eins", "Bubble zwei"]


# --- the governor -----------------------------------------------------------------------------------
def test_the_governor_refuses_a_reply_outside_active_hours(tmp_path):
    """Observed live on their rail: an outbound reply at 07:53 Europe/Berlin, because their quiet
    hours guard first touches only."""
    rig = Rig(tmp_path, moment=berlin(2026, 9, 23, 7, 53))
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(action="reply")
    assert (caught.value.code, caught.value.status_code, caught.value.retryable) == \
        ("rail_parked", 429, True)
    assert caught.value.detail["window"] == "closed"
    assert rig.driver.sent == [] and rig.driver.lock_events == []
    # Refused before the write-ahead write, so the key is untouched and stays usable.
    assert rig.ledger.get(KEY) is None
    rig.ledger.close()


def test_the_governor_blocks_sunday(tmp_path):
    rig = Rig(tmp_path, moment=berlin(2026, 9, 27, 11, 0))  # Sunday
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send()
    assert "Sunday blocked" in caught.value.message
    rig.ledger.close()


def test_the_governor_refuses_past_the_per_number_daily_cap(tmp_path):
    rig = Rig(tmp_path, per_number_cap=2)
    for i in range(2):
        rig.send(key=f"wab.o.{i:032d}")
        rig.clock.advance(60)
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(key="wab.o." + "f" * 32)
    assert caught.value.detail["cap"] == "per_number"
    assert len(rig.driver.sent) == 2
    # A different recipient is unaffected: the cap is per number, not global.
    rig.clock.advance(60)
    assert rig.send(key=KEY2, phone=OTHER)[1]["state"] == "sent"
    rig.ledger.close()


def test_the_daily_cap_is_counted_on_the_pacing_timezone_day(tmp_path):
    """Their cap resets on the UTC day while pacing runs Berlin -- 02:00 local in summer."""
    rig = Rig(tmp_path, moment=berlin(2026, 7, 1, 19, 0), per_number_cap=1)
    rig.send()
    # 22:30 UTC is still 1 July in Berlin: same day, so the cap still bites.
    rig.clock.now = berlin(2026, 7, 1, 19, 30)
    with pytest.raises(E.BridgeRefusal):
        rig.send(key=KEY2)
    # 00:30 Berlin the next day is a new Berlin day (and a new UTC day only by luck).
    rig.clock.now = berlin(2026, 7, 2, 9, 30)
    assert rig.send(key=KEY2)[1]["state"] == "sent"
    rig.ledger.close()


def test_the_first_touch_caps_are_the_colleagues_numbers(tmp_path):
    rig = Rig(tmp_path, per_number_cap=99)
    assert G.MINI_FLOOR.first_touches_per_day == 10
    assert G.MINI_FLOOR.first_touches_per_hour == 4
    for i in range(4):
        rig.send(key=f"wab.o.{i:032d}", phone=f"+4917000000{i:02d}", action="first_touch")
        rig.clock.advance(300)  # their 240-600 s gap between first touches
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(key=KEY2, phone="+491700000099", action="first_touch")
    assert caught.value.detail["cap"] == "first_touch_hourly"
    rig.ledger.close()


def test_a_request_cannot_shorten_the_gap(rig):
    rig.send()
    rig.clock.advance(1)
    with pytest.raises(E.BridgeRefusal) as caught:
        # asking for no gap at all does not lower the 4 s floor between bubbles
        rig.send(key=KEY2, constraints={"min_gap_ms": 0})
    assert caught.value.detail["reason"] == "min_gap"


def test_a_request_cannot_raise_the_cap(tmp_path):
    """A server-side bug that asks for 500 a day gets refusals, not 500 messages."""
    rig = Rig(tmp_path, per_number_cap=1)
    rig.send()
    rig.clock.advance(3600)
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(key=KEY2, constraints={"daily_cap": 500})
    assert caught.value.detail["cap"] == "per_number"
    assert len(rig.driver.sent) == 1
    rig.ledger.close()


def test_a_request_can_ask_for_a_longer_gap(rig):
    """Slower is always honoured: the request's 600 s is above the 4 s floor, so it wins."""
    rig.send()
    rig.clock.advance(60)
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(key=KEY2, constraints={"min_gap_ms": 600_000})
    assert caught.value.detail["reason"] == "min_gap"
    assert "600s" in caught.value.message


def test_a_driver_error_does_not_put_the_thread_header_on_the_wire(rig):
    """Their wrong-thread message quotes the chat header: a candidate's name or number. It stays
    in the mini-side ledger; the wire gets the class of failure and a hashed thread tag."""
    rig.driver.fail_on_open = f"wrong thread: header='Anna M.' expected {PHONE}"
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send()
    assert caught.value.status_code == 422
    assert "Anna" not in str(caught.value) and PHONE not in str(caught.value)
    assert caught.value.detail["thread"] == L.thread_tag(PHONE)
    assert "Anna M." in rig.ledger.get(KEY).detail  # on the mini, where the number already lives


def test_asking_to_bypass_quiet_hours_is_refused_not_ignored(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(constraints={"respect_quiet_hours": False})
    assert (caught.value.code, caught.value.status_code) == ("invalid_request", 400)
    assert rig.driver.sent == []


def test_an_unclassified_action_never_reaches_the_phone(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(action="whatever")
    assert (caught.value.code, caught.value.status_code) == ("invalid_request", 400)
    assert rig.driver.lock_events == []


def test_media_is_refused_because_the_rail_has_no_media_send_path(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send(kind="image")
    assert caught.value.code == "invalid_request"


# --- the flock rule ------------------------------------------------------------------------------------
def test_one_acquisition_per_bubble_released_between_bubbles(rig):
    rig.send(key=KEY, body="eins")
    rig.clock.advance(10)
    rig.send(key=KEY2, body="zwei")
    assert rig.driver.lock_events == ["acquire", "release", "acquire", "release"]
    assert rig.driver.lock_held is False


def test_the_phone_lock_is_free_while_the_brain_runs(rig):
    """The brain call happens on our VPS between two HTTP requests. Nothing may hold the flock
    there: their daemon cycles every 15 s with timeout=5."""
    seen = []
    rig.driver.hook = lambda drv: seen.append(drv.lock_held)
    rig.send(key=KEY, body="eins")
    assert seen == [True]                     # held while typing
    assert rig.driver.lock_held is False      # and free the moment the response is written
    rig.clock.advance(10)
    rig.send(key=KEY2, body="zwei")
    assert seen == [True, True]


# --- crash and reconcile ---------------------------------------------------------------------------------
def test_a_crash_leaves_attempting_which_only_a_reconcile_may_resolve(rig):
    """The process dies between the write-ahead write and the confirm."""
    sha = D.body_sha256("Guten Tag")
    rig.ledger.begin(KEY, phone=PHONE, kind="reply", body_sha256=sha, body_len=9,
                     now=rig.clock())
    assert rig.ledger.get(KEY).state == L.ATTEMPTING

    # A retry of the same key does NOT send: attempting is uncertain until reconcile says otherwise.
    rig.clock.advance(60)
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.send()
    assert caught.value.status_code == 504
    assert caught.value.detail["state"] == L.ATTEMPTING
    assert rig.driver.sent == []

    # The chat shows later outgoing traffic and no bubble of ours -> confirmed_absent.
    rig.driver.thread = [D.BubbleView("out", "etwas anderes", "23:59", "Gelesen")]
    verdicts = rig.executor.reconcile([KEY])
    assert verdicts[0]["verdict"] == "confirmed_absent"
    assert rig.ledger.get(KEY).state == L.ABSENT

    # Only now may the same key be sent, and it sends exactly once.
    rig.clock.advance(60)
    assert rig.send()[1]["state"] == "sent"
    assert rig.driver.sent == ["Guten Tag"]


def test_reconcile_will_not_call_absent_when_the_view_does_not_cover_the_attempt(rig):
    sha = D.body_sha256("Guten Tag")
    rig.ledger.begin(KEY, phone=PHONE, kind="reply", body_sha256=sha, body_len=9, now=rig.clock())
    rig.driver.thread = [D.BubbleView("out", "alte Nachricht", "08:00", "Gelesen")]
    verdict = rig.executor.reconcile([KEY])[0]
    assert verdict["verdict"] == "indeterminate"
    assert rig.ledger.get(KEY).state == L.ATTEMPTING  # unchanged: a question, not a verdict


def test_reconcile_promotes_a_body_match_that_carries_a_tick(rig):
    sha = D.body_sha256("Guten Tag")
    rig.ledger.begin(KEY, phone=PHONE, kind="reply", body_sha256=sha, body_len=9, now=rig.clock())
    rig.driver.thread = [D.BubbleView("out", "Guten Tag", "10:01", "Zugestellt")]
    verdict = rig.executor.reconcile([KEY])[0]
    assert verdict["verdict"] == "confirmed_sent"
    assert rig.ledger.get(KEY).state == L.SENT


def test_reconcile_leaves_a_matching_bubble_without_a_tick_indeterminate(rig):
    sha = D.body_sha256("Guten Tag")
    rig.ledger.begin(KEY, phone=PHONE, kind="reply", body_sha256=sha, body_len=9, now=rig.clock())
    rig.driver.thread = [D.BubbleView("out", "Guten Tag", "10:01", "")]
    assert rig.executor.reconcile([KEY])[0]["verdict"] == "indeterminate"


# --- inbound handover, health, retention ---------------------------------------------------------------
def _inbound(text="Ja, gerne", phone=PHONE, clock="10:04", media=None):
    return I.assign_ids([I.InboundMessage(counterparty=phone, title=phone, text=text,
                                          local_date="2026-09-23", clock=clock,
                                          source="notification", time_ms=1789312500000,
                                          media=media)])


def test_the_outbox_is_a_durable_pull_with_a_cursor(rig):
    assert rig.executor.record_inbound(_inbound(), [], rig.clock())["stored"] == 1
    # the same notification on the next poll is the same id, so it is one event
    assert rig.executor.record_inbound(_inbound(), [], rig.clock())["stored"] == 0

    pulled = rig.executor.outbox()
    assert [e["payload"]["text"] for e in pulled["events"]] == ["Ja, gerne"]
    assert pulled["backlog"]["unacked"] == 1
    acked = rig.executor.outbox(after=pulled["cursor"], ack=pulled["cursor"])
    assert acked["events"] == [] and acked["backlog"]["unacked"] == 0


def test_an_unresolvable_inbound_is_journalled_and_never_wedges_the_cursor(rig):
    """No number means no ``from`` for the envelope, so it must not enter the outbox -- an item the
    relay can never deliver would stop every later message behind it."""
    result = rig.executor.record_inbound([], [("Oma", "address book has no number")], rig.clock())
    assert result == {"stored": 0, "seen": 0, "unresolved": 1}
    assert rig.executor.outbox()["events"] == []
    assert rig.executor.health()["inbound"]["unresolved"] == 1


# --- inbound media (TASK-131 round 5, decision-9 2026-09-22: automatic attribution removed) -------
def test_an_unattached_media_message_is_released_as_its_placeholder_immediately(rig):
    """No hold any more (round 5 deleted MEDIA_HOLD_SEC and the automatic linker it existed for):
    a media message is released exactly like any other message, the moment it is polled."""
    msg = _inbound(text="\U0001f4c4 Dokument", media="document")
    rig.executor.record_inbound(msg, [], rig.clock())
    events = rig.executor.outbox()["events"]
    assert len(events) == 1
    assert events[0]["payload"]["text"] == "\U0001f4c4 Dokument"
    assert "media_id" not in events[0]["payload"]


def test_attaching_a_file_by_hand_merges_into_the_event_it_mints_not_the_old_placeholder(rig):
    """Round 5's attach mints its OWN inbound event (bridge/ledger.py::attach_media) rather than
    patching the original placeholder -- see that test section below. The original placeholder row
    stays exactly what it was; the new event carries the file."""
    msg = _inbound(text="\U0001f4c4 Dokument", media="document")
    rig.executor.record_inbound(msg, [], rig.clock())
    rig.ledger.record_media(source_rel="WhatsApp Documents/x.pdf", mtime=0, media_id="wab.m.aaaa",
                            sha256="a" * 64, local_path="/tmp/x.pdf", size=10, kind="document",
                            mime_type="application/pdf", filename="Lebenslauf.pdf", now=rig.clock())
    [queued] = rig.ledger.media_queue()
    rig.executor.attach_media(queued["queue_id"], PHONE)

    events = rig.executor.outbox()["events"]
    assert len(events) == 2
    assert events[0]["payload"]["text"] == "\U0001f4c4 Dokument" and \
        "media_id" not in events[0]["payload"], "the original placeholder, untouched"
    assert events[1]["payload"]["media_id"] == "wab.m.aaaa"
    assert events[1]["payload"]["media_filename"] == "Lebenslauf.pdf"


def test_a_text_message_is_never_held_back(rig):
    assert rig.executor.record_inbound(_inbound(), [], rig.clock())["stored"] == 1
    assert len(rig.executor.outbox()["events"]) == 1


# --- GET /v1/media/<id>, GET /v1/media/<id>/raw (TASK-131) -----------------------------------------
def test_media_metadata_and_raw_bytes_round_trip(http, tmp_path):
    rig, base = http
    blob = b"%PDF-1.4 fake bytes"
    path = tmp_path / "store" / "wab.m.aaaa"
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)
    rig.ledger.record_media(source_rel="WhatsApp Documents/x.pdf", mtime=0, media_id="wab.m.aaaa",
                            sha256="a" * 64, local_path=str(path), size=len(blob), kind="document",
                            mime_type="application/pdf", filename="Lebenslauf.pdf", now=rig.clock())

    status, meta = call(base, "/v1/media/wab.m.aaaa")
    assert status == 200
    assert (meta["mime_type"], meta["filename"], meta["size"]) == \
        ("application/pdf", "Lebenslauf.pdf", len(blob))
    assert meta["url"] == "/v1/media/wab.m.aaaa/raw"

    req = urllib.request.Request(base + meta["url"],
                                 headers={"Authorization": "Bearer s3cret"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.read() == blob
        assert resp.headers["Content-Type"] == "application/pdf"


def test_an_unknown_media_id_is_a_404(http):
    _rig, base = http
    status, body = call(base, "/v1/media/wab.m.never-pulled")
    assert (status, body["error"]["code"]) == (404, "media_not_found")
    status, body = call(base, "/v1/media/wab.m.never-pulled/raw")
    assert (status, body["error"]["code"]) == (404, "media_not_found")


def test_a_recorded_file_missing_from_disk_is_a_loud_500_not_an_empty_document(http):
    rig, base = http
    rig.ledger.record_media(source_rel="WhatsApp Documents/gone.pdf", mtime=0,
                            media_id="wab.m.gone", sha256="b" * 64,
                            local_path="/nonexistent/pflege-bridge-test/gone.pdf", size=5,
                            kind="document", mime_type="application/pdf", filename="gone.pdf",
                            now=rig.clock())
    status, body = call(base, "/v1/media/wab.m.gone/raw")
    assert status == 500 and body["error"]["code"] == "executor_error"
    assert "missing" in body["error"]["message"] or "could not be read" in body["error"]["message"]


# --- the media watcher: pulls into the queue, links nothing (TASK-131 round 5) ----------------------
def test_the_media_watcher_only_pulls_into_the_queue_and_never_pulls_a_path_twice(rig, tmp_path):
    msg = _inbound(text="\U0001f4c4 Dokument", media="document")
    rig.executor.record_inbound(msg, [], rig.clock())
    rig.driver.media_files["WhatsApp Documents/Lebenslauf.pdf"] = b"%PDF-1.4 bytes"
    rig.driver.media_mtimes["WhatsApp Documents/Lebenslauf.pdf"] = int(msg[0].time_ms / 1000)

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    assert watch.cycle() == {"pulled": 1}
    [row] = rig.ledger.media_queue()
    assert row["kind"] == "document" and row["attached_at"] is None
    assert rig.ledger.media_file(row["media_id"])["filename"] == "Lebenslauf.pdf"

    # a second cycle sees the same file on the handset and must not re-pull it
    assert watch.cycle() == {"pulled": 0}
    assert rig.driver.pulled_media == ["WhatsApp Documents/Lebenslauf.pdf"]


def test_a_voice_note_is_queued_without_any_chat_read(rig, tmp_path):
    """A voice note has no text node a chat read could ever consult (round 3's now-deleted oracle
    could never fire for one at all); round 5 asks a chat nothing about ANY kind -- pulling touches
    no lock and opens no chat, full stop."""
    msg = _inbound(text="\U0001f3a4 Sprachnachricht", media="audio")
    rig.executor.record_inbound(msg, [], rig.clock())
    rig.driver.media_files["WhatsApp Voice Notes/PTT-20260913.opus"] = b"opus bytes"
    rig.driver.media_mtimes["WhatsApp Voice Notes/PTT-20260913.opus"] = int(msg[0].time_ms / 1000)
    rig.driver.busy = True   # a send holds huawei01.lock throughout -- must not matter either

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    assert watch.cycle() == {"pulled": 1}
    [row] = rig.ledger.media_queue()
    assert row["kind"] == "audio"
    assert rig.driver.opened == [], "no chat was ever opened"


def test_a_repeat_cycle_never_ingests_the_same_file_twice(rig, tmp_path):
    rig.driver.media_files["WhatsApp Documents/CV.pdf"] = b"%PDF-1.4 bytes"
    rig.driver.media_mtimes["WhatsApp Documents/CV.pdf"] = 0

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    watch.cycle()
    watch.cycle()
    watch.cycle()

    assert rig.driver.pulled_media == ["WhatsApp Documents/CV.pdf"]
    assert watch.pulled_total == 1
    assert rig.ledger.media_backlog(rig.clock())["unresolved"] == 1


def test_a_queued_file_is_never_dropped_however_old_it_gets(rig, tmp_path):
    """No retirement any more (round 5 -- "keep only what the queue genuinely needs"): the whole
    point of the queue is that nothing in it is ever lost or made invisible, however long it sits."""
    rig.driver.media_files["WhatsApp Documents/Old.pdf"] = b"%PDF-1.4 bytes"
    rig.driver.media_mtimes["WhatsApp Documents/Old.pdf"] = 0

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    assert watch.cycle() == {"pulled": 1}
    rig.clock.advance(60 * 60 * 24 * 400)   # far past the old (now-deleted) retirement horizon
    assert watch.cycle() == {"pulled": 0}
    assert rig.ledger.media_backlog(rig.clock())["unresolved"] == 1
    assert len(rig.ledger.media_queue()) == 1


def test_health_reports_the_queue_backlog(rig, tmp_path):
    rig.driver.media_files["WhatsApp Documents/Orphan.pdf"] = b"%PDF-1.4 bytes"
    rig.driver.media_mtimes["WhatsApp Documents/Orphan.pdf"] = 0

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    rig.executor.media_watcher = watch
    watch.cycle()

    backlog = rig.executor.health()["media_watcher"]["unresolved_backlog"]
    assert backlog == {"unresolved": 1, "oldest_unresolved_sec": backlog["oldest_unresolved_sec"],
                       "by_kind": {"document": 1}, "duplicate_content": 0, "weak_links": 0}
    assert backlog["oldest_unresolved_sec"] >= 0


def test_media_watcher_errors_are_counted_and_never_raise(rig, tmp_path):
    class BrokenMediaDriver(D.FakeDriver):
        def list_media(self):
            raise RuntimeError("adb gone")

    watch = W.MediaWatcher(BrokenMediaDriver(), rig.ledger, tmp_path / "media",
                           log=lambda _m: None, clock=rig.clock)
    assert watch.cycle() is None
    assert watch.errors == 1 and "adb gone" in watch.last_error


def test_health_reports_the_media_watchers_heartbeat(rig, tmp_path):
    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    rig.executor.media_watcher = watch
    watch.cycle()
    assert rig.executor.health()["media_watcher"]["cycles"] == 1


def test_health_reports_no_media_watcher_when_none_is_wired(rig):
    assert rig.executor.health()["media_watcher"] is None


# --- no automatic attachment exists any more, structurally (TASK-131 round 5 requirement 1) ---------
def test_no_ledger_method_left_that_an_automatic_matcher_could_even_call(rig):
    """Delete the inference machinery rather than leave it dormant (round 5's own instruction): a
    dead method still importable would teach the next person something here is still guarded."""
    for name in ("pending_media", "unresolved_media", "retire_stale_media", "record_media_outcome",
                "shade_last_ok_epoch", "record_shade_read"):
        assert not hasattr(rig.ledger, name), f"Ledger.{name} should not exist any more"


# --- the four failures the last verification found, now structurally absent (round 5 requirement 1) -
def test_decoy_a_download_finishing_outside_the_old_window_never_causes_a_wrong_attach(rig, tmp_path):
    """Round 4's window was a 180s pre-filter; a slow auto-download landing well outside it, with an
    unrelated same-kind candidate sitting conveniently close to the DRIFTED mtime, risked a wrong
    attach if that candidate happened to be the sole window survivor. There is no window left to
    drift past: the file simply queues, and nothing in this package ever creates a link, regardless
    of who else is pending nearby."""
    real_sender = _inbound(text="\U0001f4c4 Dokument", phone=PHONE, clock="17:15", media="document")
    rig.executor.record_inbound(real_sender, [], rig.clock())
    decoy = _inbound(text="\U0001f4c4 Dokument", phone=OTHER, clock="18:00", media="document")
    rig.executor.record_inbound(decoy, [], rig.clock())

    rig.driver.media_files["WhatsApp Documents/Slow-CV.pdf"] = b"%PDF-1.4 bytes"
    # lands 900s after its own sender's notification -- well outside the old 180s window, and much
    # closer to the unrelated decoy candidate's own notification time instead
    rig.driver.media_mtimes["WhatsApp Documents/Slow-CV.pdf"] = \
        int(real_sender[0].time_ms / 1000) + 900

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    for _ in range(3):
        watch.cycle()
    [row] = rig.ledger.media_queue()
    assert row["attached_at"] is None
    assert rig.executor.unresolved_media()["count"] == 1


def test_decoy_a_kind_mismatch_between_the_extension_and_the_placeholder_never_attaches(rig, tmp_path):
    """WhatsApp's notification classifies by message kind ('document'), not by what the bytes
    actually are; a photo forwarded AS a document is still a .jpg on disk. Round 4 keyed matching on
    both sides agreeing on a kind, which either stranded such a file forever or, worse, could align
    by coincidence with an unrelated candidate of the kind the EXTENSION says. Round 5 does not
    compare kinds to any notification at all: the file queues with its own true kind (read off its
    own extension) and nothing tries to reconcile it with what a notification claimed."""
    msg = _inbound(text="\U0001f4c4 Dokument", phone=PHONE, media="document")
    rig.executor.record_inbound(msg, [], rig.clock())
    # forwarded as a "document" bubble, but the bytes are a photo
    path = "WhatsApp Documents/photo_forwarded_as_document.jpg"
    rig.driver.media_files[path] = b"\xff\xd8\xff bytes"
    rig.driver.media_mtimes[path] = int(msg[0].time_ms / 1000)

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    watch.cycle()
    [row] = rig.ledger.media_queue()
    assert row["kind"] == "image", "the file's own extension, not the notification's claimed kind"
    assert row["attached_at"] is None


def test_decoy_a_cross_host_clock_skew_never_attaches_or_skews_the_queues_own_age(rig, tmp_path):
    """Round 4's catch-up gate compared the mini's own clock (when the shade was last read) against
    the HANDSET's own stat mtime -- two clocks never guaranteed to agree, as the module's own
    docstring said. There is no such comparison left at all: a wildly skewed handset mtime (a year
    ahead of the mini's own clock here) neither blocks nor causes an attach, and the queue's own age
    is read off the LEDGER's own wall clock at pull time, never off the handset's."""
    rig.driver.media_files["WhatsApp Documents/Skewed.pdf"] = b"%PDF-1.4 bytes"
    rig.driver.media_mtimes["WhatsApp Documents/Skewed.pdf"] = \
        int(rig.clock.now.timestamp()) + 60 * 60 * 24 * 365

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    watch.cycle()
    [entry] = rig.executor.unresolved_media()["files"]
    assert entry["age_sec"] < 5, "age comes off the ledger's own pull time, not the skewed mtime"
    assert entry["kind"] == "document"


def test_identical_bytes_from_two_senders_are_two_queue_entries_not_one(rig, tmp_path):
    """THE SILENT-LOSS BUG THIS ROUND FIXES: round 4 kept one queue row per CONTENT id, so the
    second of two byte-identical files (a shared template CV from two different candidates) vanished
    the moment the first one's content id was ever linked -- gone from the links, the queue and
    health alike. Round 5 keys the queue on the pull instance, never on the content."""
    rig.driver.media_files["WhatsApp Documents/Anna-CV.pdf"] = b"%PDF-1.4 identical bytes"
    rig.driver.media_files["WhatsApp Documents/Bernd-CV.pdf"] = b"%PDF-1.4 identical bytes"
    rig.driver.media_mtimes["WhatsApp Documents/Anna-CV.pdf"] = 100
    rig.driver.media_mtimes["WhatsApp Documents/Bernd-CV.pdf"] = 200

    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    rig.executor.media_watcher = watch
    assert watch.cycle() == {"pulled": 2}

    queue = rig.ledger.media_queue()
    assert len(queue) == 2, "two pulls, two queue entries -- even though the bytes are identical"
    assert len({row["queue_id"] for row in queue}) == 2
    assert len({row["media_id"] for row in queue}) == 1, "one media_id: the bytes really are shared"
    assert rig.executor.unresolved_media()["count"] == 2
    assert rig.executor.health()["media_watcher"]["unresolved_backlog"]["unresolved"] == 2

    store_dir = tmp_path / "media" / "store"
    assert len(list(store_dir.iterdir())) == 1, "the bytes are still stored only once on disk"

    # attaching one leaves the other -- untouched, still in the queue, independently attachable
    rig.executor.attach_media(queue[0]["queue_id"], PHONE)
    remaining = rig.ledger.media_queue()
    assert len(remaining) == 1 and remaining[0]["queue_id"] == queue[1]["queue_id"]


# --- the human escape hatch (TASK-131 round 6: the fallback, not the front door) --------------------
def test_unresolved_media_lists_facts_and_nothing_identifying(rig):
    rig.ledger.record_media(source_rel="WhatsApp Documents/Anna Musterfrau Lebenslauf.pdf", mtime=0,
                            media_id="wab.m.orphan", sha256="9" * 64, local_path="/tmp/orphan.pdf",
                            size=42, kind="document", mime_type="application/pdf",
                            filename="Anna Musterfrau Lebenslauf.pdf", now=rig.clock())

    listing = rig.executor.unresolved_media()
    assert listing["count"] == 1
    [row] = listing["files"]
    assert row["media_id"] == "wab.m.orphan"
    assert row["kind"] == "document" and row["size"] == 42
    assert row["source_dir"] == "WhatsApp Documents"
    assert row["age_sec"] >= 0
    assert row["related_threads"] == []
    assert row["content_pull_count"] == 1
    assert set(row) == {"queue_id", "media_id", "kind", "size", "source_dir", "pulled_at",
                        "age_sec", "related_threads", "content_pull_count"}, \
        "no filename, no phone -- facts (and a hint) only"


def test_unresolved_media_names_plausibly_related_threads_as_a_hint_not_a_decision(rig):
    msg = _inbound(text="\U0001f4c4 Dokument", phone=PHONE, media="document")
    rig.executor.record_inbound(msg, [], rig.clock())
    rig.ledger.record_media(source_rel="WhatsApp Documents/x.pdf", mtime=int(msg[0].time_ms / 1000),
                            media_id="wab.m.aaaa", sha256="a" * 64, local_path="/tmp/x.pdf",
                            size=10, kind="document", mime_type="application/pdf",
                            filename="Lebenslauf.pdf", now=rig.clock())
    [row] = rig.executor.unresolved_media()["files"]
    assert row["related_threads"] == [L.thread_tag(PHONE)], "a hint the operator can check -- never used to attach"


def test_attach_media_delivers_the_document_to_the_right_thread(rig):
    """Acceptance: the manual attach delivers the document to the named thread -- through the EXACT
    SAME call (bridge/ledger.py::link_media) an automatic link used to make, before round 5 removed
    automatic linking entirely -- so the outbox merge is exactly what a real link always produced."""
    rig.ledger.record_media(source_rel="WhatsApp Documents/x.pdf", mtime=0, media_id="wab.m.aaaa",
                            sha256="a" * 64, local_path="/tmp/x.pdf", size=10, kind="document",
                            mime_type="application/pdf", filename="Lebenslauf.pdf", now=rig.clock())
    [queued] = rig.ledger.media_queue()

    report = rig.executor.attach_media(queued["queue_id"], PHONE)
    assert report["media_id"] == "wab.m.aaaa" and report["kind"] == "document"
    assert report["thread"] == L.thread_tag(PHONE)
    assert rig.ledger.media_queue() == [], "the queue no longer offers it"

    events = rig.executor.outbox()["events"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["media_id"] == "wab.m.aaaa"
    assert payload["media_filename"] == "Lebenslauf.pdf"
    assert payload["from"] == PHONE


def test_attach_media_works_with_no_pending_placeholder_message_at_all(rig):
    """THE SIX FILES ALREADY ON THE LIVE RAIL (round 5 requirement 4): a file whose own placeholder
    message was drained and swept off this ledger long ago -- or one that arrived before this ledger
    ever recorded an inbound row for it -- is attachable from the file id alone. Attach never looks
    for a pending message any more; it mints the event itself."""
    rig.ledger.record_media(source_rel="WhatsApp Documents/x.pdf", mtime=0, media_id="wab.m.aaaa",
                            sha256="a" * 64, local_path="/tmp/x.pdf", size=10, kind="document",
                            mime_type="application/pdf", filename="Lebenslauf.pdf", now=rig.clock())
    assert rig.executor.outbox()["events"] == [], "nothing pending, no placeholder, nothing at all"
    [queued] = rig.ledger.media_queue()

    report = rig.executor.attach_media(queued["queue_id"], PHONE)
    assert report["thread"] == L.thread_tag(PHONE)
    events = rig.executor.outbox()["events"]
    assert len(events) == 1 and events[0]["payload"]["media_id"] == "wab.m.aaaa"


def test_attach_media_an_unknown_id_is_media_not_found(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.attach_media("wab.q.never-pulled", PHONE)
    assert (caught.value.code, caught.value.status_code) == ("media_not_found", 404)


def test_attach_media_twice_is_refused_as_already_attached(rig):
    rig.ledger.record_media(source_rel="WhatsApp Documents/x.pdf", mtime=0, media_id="wab.m.aaaa",
                            sha256="a" * 64, local_path="/tmp/x.pdf", size=10, kind="document",
                            mime_type="application/pdf", filename="Lebenslauf.pdf", now=rig.clock())
    [queued] = rig.ledger.media_queue()
    rig.executor.attach_media(queued["queue_id"], PHONE)
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.attach_media(queued["queue_id"], OTHER)
    assert (caught.value.code, caught.value.status_code) == ("already_attached", 409)


def test_a_voice_note_attached_by_hand_reaches_the_shape_transcription_reads(rig):
    """app/wa/api.py transcribes off a Meta ``audio`` message object (out of this lane); what this
    lane owns is the shape that reaches it -- bridge/envelope.py::meta_envelope, fed the same
    outbox payload attach_media produces."""
    rig.ledger.record_media(source_rel="WhatsApp Voice Notes/PTT-1.opus", mtime=0,
                            media_id="wab.m.voice", sha256="v" * 64, local_path="/tmp/v.opus",
                            size=2048, kind="audio", mime_type="audio/ogg", filename="PTT-1.opus",
                            now=rig.clock())
    [queued] = rig.ledger.media_queue()
    rig.executor.attach_media(queued["queue_id"], PHONE)

    [event] = rig.executor.outbox()["events"]
    envelope = EN.meta_envelope(event["payload"], phone_number_id="pnid", display_phone_number="+1",
                               waba_id="waba")
    message = envelope["entry"][0]["changes"][0]["value"]["messages"][0]
    assert message["type"] == "audio"
    assert message["audio"]["id"] == "wab.m.voice"


def test_the_queue_survives_a_restart(tmp_path):
    """The queue lives in the ledger's own sqlite file, not in the watcher's memory -- a restart
    (fresh Python objects, same file underneath) must not lose or duplicate a single row."""
    path = tmp_path / "ledger.sqlite"
    now = datetime(2026, 9, 22, 10, 0, 0, tzinfo=timezone.utc)
    first = L.Ledger(str(path))
    first.record_media(source_rel="WhatsApp Documents/x.pdf", mtime=0, media_id="wab.m.aaaa",
                       sha256="a" * 64, local_path="/tmp/x.pdf", size=10, kind="document",
                       mime_type="application/pdf", filename="Lebenslauf.pdf", now=now)
    first.close()

    reborn = L.Ledger(str(path))
    queue = reborn.media_queue()
    assert len(queue) == 1 and queue[0]["media_id"] == "wab.m.aaaa"
    reborn.close()


# --- ALSO FIX: a duplicate is visible, and the six pre-existing rows are reachable (round 6) -------
def test_a_byte_identical_duplicate_pull_is_visible_on_the_queue_row(rig, tmp_path):
    """Ivan does not expect two people to send byte-identical bytes -- but if it happens anyway, it
    must be visible, not silently folded into whichever pull got stored first."""
    rig.driver.media_files["WhatsApp Documents/Anna-CV.pdf"] = b"same bytes twice"
    rig.driver.media_files["WhatsApp Documents/Bernd-CV.pdf"] = b"same bytes twice"
    watch = W.MediaWatcher(rig.driver, rig.ledger, tmp_path / "media", log=lambda _m: None,
                           clock=rig.clock)
    rig.executor.media_watcher = watch
    watch.cycle()

    assert len(rig.ledger.media_queue()) == 2
    assert all(row["content_pull_count"] == 2 for row in rig.ledger.media_queue())
    assert rig.executor.health()["media_watcher"]["unresolved_backlog"]["duplicate_content"] == 1

    rig.driver.media_files["WhatsApp Documents/Solo.pdf"] = b"nobody else sent this"
    watch.cycle()
    solo = [r for r in rig.ledger.media_queue() if r["content_pull_count"] == 1]
    assert len(solo) == 1


def test_the_six_legacy_rows_from_before_the_queue_columns_existed_are_reachable(tmp_path):
    """Reproduces the exact pre-round-5 schema found live on the mini 2026-09-22: ``media_seen``
    with only (source_rel, media_id, size, mtime, seen_at) -- no queue_id/kind/source_dir/attach
    columns at all, six rows from an earlier build. The migration must backfill kind (derivable from
    the handset path) so these rows stop being unreachable, without inventing an mtime they already
    carry."""
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    raw = sqlite3.connect(str(path))
    raw.execute("create table media_seen (source_rel text primary key, media_id text not null, "
               "size integer not null, mtime integer not null, seen_at text not null)")
    raw.execute("insert into media_seen values (?,?,?,?,?)",
               ("WhatsApp Voice Notes/202632/PTT-20260804-WA0001.opus", "wab.m.legacy1", 165803,
                1785858224, "2026-09-22T08:02:05.680Z"))
    raw.execute("insert into media_seen values (?,?,?,?,?)",
               ("WhatsApp Images/Private/IMG-20260805-WA0000.jpg", "wab.m.legacy2", 122272,
                1785928218, "2026-09-22T08:02:05.680Z"))
    raw.commit()
    raw.close()

    ledger = L.Ledger(str(path))
    try:
        queue = {row["media_id"]: row for row in ledger.media_queue()}
        assert set(queue) == {"wab.m.legacy1", "wab.m.legacy2"}
        assert queue["wab.m.legacy1"]["kind"] == "audio"
        assert queue["wab.m.legacy1"]["mtime"] == 1785858224, "the real stat mtime, not invented"
        assert queue["wab.m.legacy2"]["kind"] == "image"
        assert queue["wab.m.legacy1"]["legacy"] == 1 and queue["wab.m.legacy2"]["legacy"] == 1
        assert ledger.media_queue(auto_only=True) == []
        # reachable end to end: the human escape hatch (and, by the same call, an automatic match)
        # can attach either one now that it carries a kind.
        ledger.attach_media(queue["wab.m.legacy1"]["queue_id"], PHONE, now=berlin(2026, 9, 22, 10))
    finally:
        ledger.close()


def test_a_database_already_migrated_by_round_6_alone_still_gets_its_rows_flagged_legacy(tmp_path):
    """Reproduces the EXACT live state found on the mini 2026-09-22 (TASK-131 round 7 fix): round 6
    already ran once and backfilled queue_id/kind/source_dir for the six pre-round-5 rows -- so by
    the time round 7's ``legacy`` column ships, ``where queue_id is null`` (the only signal the
    original backfill had) finds nothing, and the six rows would silently stay legacy=0, exactly as
    they did live before this fix. The very first migration that ever adds the ``legacy`` column at
    all must catch them anyway."""
    import sqlite3

    path = tmp_path / "round6_only.sqlite"
    raw = sqlite3.connect(str(path))
    # The round-6 schema: every queue column round 6 shipped, but not `legacy` (round 7's own
    # addition) -- and queue_id already backfilled, the same as this mini's own database.
    raw.execute("create table media_seen (source_rel text primary key, media_id text not null, "
               "size integer not null, mtime integer not null, seen_at text not null, "
               "queue_id text, kind text, source_dir text, attached_at text, "
               "attached_inbound_id text, attached_phone text, link_strength text, link_reason text)")
    raw.execute("insert into media_seen(source_rel, media_id, size, mtime, seen_at, queue_id, kind, "
               "source_dir) values (?,?,?,?,?,?,?,?)",
               ("WhatsApp Images/Private/IMG-20260805-WA0000.jpg", "wab.m.r6only", 122272,
                1785928218, "2026-09-22T08:02:05.680Z", "wab.q.already-backfilled", "image",
                "WhatsApp Images/Private"))
    raw.commit()
    raw.close()

    ledger = L.Ledger(str(path))
    try:
        [row] = ledger.media_queue()
        assert row["legacy"] == 1, "queue_id was already set by round 6 -- must still be caught"
        assert ledger.media_queue(auto_only=True) == []
    finally:
        ledger.close()


# --- automatic identity matching (TASK-131 round 6, Ivan's ruling 2026-09-22) ----------------------
def _seed_media(rig, *, kind, size, filename, local_path="/tmp/x", media_id="wab.m.x", source_rel=None):
    rig.ledger.record_media(source_rel=source_rel or f"WhatsApp {kind}/{filename}",
                            mtime=int(rig.clock().timestamp()), media_id=media_id, sha256=media_id * 2,
                            local_path=local_path, size=size, kind=kind,
                            mime_type="application/octet-stream", filename=filename, now=rig.clock())
    [row] = [r for r in rig.ledger.media_queue() if r["media_id"] == media_id]
    return row


def _write_opus(path, seconds):
    """A minimal, valid two-page Ogg/Opus file whose last granule is exactly ``seconds`` -- enough
    for bridge/identity.py::opus_duration_seconds_of_file to read back, nothing else about Opus."""
    import struct

    def page(granule, payload, seq, *, first=False, last=False):
        header_type = (0x02 if first else 0) | (0x04 if last else 0)
        head = struct.pack("<4sBBqIIIB", b"OggS", 0, header_type, granule, 1, seq, 0, 1)
        return head + bytes([len(payload)]) + payload

    blob = page(0, b"OpusHead" + b"\x00" * 10, 0, first=True)
    blob += page(int(seconds * 48000), b"\x00" * 10, 1, last=True)
    path.write_bytes(blob)


def test_sole_candidate_of_a_kind_is_attached_strong_with_no_chat_ever_opened(rig):
    _seed_media(rig, kind="image", size=1000, filename="IMG-1.jpg")
    rig.executor.record_inbound(_inbound(media="image", phone=PHONE), [], rig.clock())

    assert rig.executor.auto_match_media() == {"attached": 1, "weak": 0}
    assert rig.ledger.media_queue() == []
    assert rig.driver.opened == [], "a sole candidate needs no bubble read at all"
    [event] = rig.executor.outbox()["events"]
    assert event["payload"]["from"] == PHONE and event["payload"]["media_link_strength"] == "strong"


def test_two_candidates_in_one_chat_minute_separated_by_size_not_time(rig):
    """THE ACCEPTANCE CASE: two files, one chat minute, told apart because an attribute (size)
    differs -- matching by time alone would have been unable to choose."""
    _seed_media(rig, kind="document", size=165000, filename="Anna.pdf", media_id="wab.m.doc1")
    rig.executor.record_inbound(_inbound(media="document", phone=PHONE, clock="10:04"), [], rig.clock())
    rig.executor.record_inbound(_inbound(media="document", phone=OTHER, clock="10:04"), [], rig.clock())
    rig.driver.media_evidence_by_phone = {
        PHONE: [{"clock": "10:04", "evidence": ["Anna.pdf", "165 KB"]}],
        OTHER: [{"clock": "10:04", "evidence": ["Bernd.pdf", "900 KB"]}],
    }

    assert rig.executor.auto_match_media() == {"attached": 1, "weak": 0}
    [event] = [e for e in rig.executor.outbox()["events"] if e["payload"].get("media_id")]
    assert event["payload"]["from"] == PHONE and event["payload"]["media_link_strength"] == "strong"


def test_a_voice_note_is_matched_by_duration(rig, tmp_path):
    audio = tmp_path / "v.opus"
    _write_opus(audio, 7.0)
    _seed_media(rig, kind="audio", size=audio.stat().st_size, filename="PTT-1.opus",
               local_path=str(audio), media_id="wab.m.voice1")
    rig.executor.record_inbound(_inbound(media="audio", phone=PHONE, clock="11:02"), [], rig.clock())
    rig.executor.record_inbound(_inbound(media="audio", phone=OTHER, clock="11:02"), [], rig.clock())
    rig.driver.media_evidence_by_phone = {
        PHONE: [{"clock": "11:02", "evidence": ["Sprachnachricht", "0:42"]}],
        OTHER: [{"clock": "11:02", "evidence": ["Sprachnachricht", "0:07"]}],
    }

    assert rig.executor.auto_match_media() == {"attached": 1, "weak": 0}
    [event] = [e for e in rig.executor.outbox()["events"] if e["payload"].get("media_id")]
    assert event["payload"]["from"] == OTHER and event["payload"]["media_link_strength"] == "strong"


def test_the_newest_bubble_in_a_thread_supplies_the_evidence_not_the_oldest(rig, tmp_path):
    """TASK-131 round 6 blocker B1, fixed: a chat that already holds an OLD voice note (already
    resolved in an earlier cycle, or just old scrollback) must not answer for today's file with that
    old bubble's duration. PHONE's newest bubble (1:30) does not match; OTHER's newest bubble (0:07)
    does -- OTHER wins, even though PHONE's OLDEST bubble also happened to read 0:07."""
    audio = tmp_path / "v.opus"
    _write_opus(audio, 7.0)
    _seed_media(rig, kind="audio", size=audio.stat().st_size, filename="PTT-9.opus",
               local_path=str(audio), media_id="wab.m.v7")
    rig.executor.record_inbound(_inbound(media="audio", phone=PHONE, clock="11:02"), [], rig.clock())
    rig.executor.record_inbound(_inbound(media="audio", phone=OTHER, clock="11:02"), [], rig.clock())
    rig.driver.media_evidence_by_phone = {
        # A: an old 0:07 voice note from last week (already resolved), today's real one at 1:30
        PHONE: [{"clock": "09:00", "evidence": ["Sprachnachricht", "0:07"]},
                {"clock": "11:02", "evidence": ["Sprachnachricht", "1:30"]}],
        # B: an old 0:55, and today's real one -- the 7-second file we are attributing
        OTHER: [{"clock": "09:10", "evidence": ["Sprachnachricht", "0:55"]},
                {"clock": "11:02", "evidence": ["Sprachnachricht", "0:07"]}],
    }
    assert rig.executor.auto_match_media() == {"attached": 1, "weak": 0}
    [event] = [e for e in rig.executor.outbox()["events"] if e["payload"].get("media_id")]
    assert event["payload"]["from"] == OTHER and event["payload"]["media_link_strength"] == "strong"


def test_a_sole_candidate_contradicted_by_its_own_bubble_attaches_weak_not_strong(rig, tmp_path):
    """TASK-131 round 6 blocker B2, fixed: a sole candidate is no longer unconditionally strong.
    Its own thread reads 2:30 for a file whose bytes are 7 seconds -- that is read (audio is an
    evidence-bearing kind) and disagreed with, so the pick is weak and audited, not silently strong."""
    audio = tmp_path / "v.opus"
    _write_opus(audio, 7.0)
    _seed_media(rig, kind="audio", size=audio.stat().st_size, filename="PTT-9.opus",
               local_path=str(audio), media_id="wab.m.contra")
    rig.executor.record_inbound(_inbound(media="audio", phone=PHONE, clock="11:02"), [], rig.clock())
    rig.driver.media_evidence_by_phone = {PHONE: [{"clock": "11:02",
                                                   "evidence": ["Sprachnachricht", "2:30"]}]}
    assert rig.executor.auto_match_media() == {"attached": 1, "weak": 1}
    phone, strength, reason = [(r["attached_phone"], r["link_strength"], r["link_reason"])
                               for r in [dict(x) for x in
                                        rig.ledger._db.execute("select * from media_seen").fetchall()]
                               if r["media_id"] == "wab.m.contra"][0]
    assert (phone, strength, reason) == (PHONE, "weak", "sole_candidate_contradicted")
    assert rig.driver.opened == [PHONE], "an evidence-bearing kind's sole candidate IS checked"


def test_a_sole_candidate_confirmed_by_its_own_bubble_stays_strong(rig, tmp_path):
    """The same check, agreeing this time: still strong, still audited as a real confirmation."""
    audio = tmp_path / "v.opus"
    _write_opus(audio, 7.0)
    _seed_media(rig, kind="audio", size=audio.stat().st_size, filename="PTT-9.opus",
               local_path=str(audio), media_id="wab.m.agree")
    rig.executor.record_inbound(_inbound(media="audio", phone=PHONE, clock="11:02"), [], rig.clock())
    rig.driver.media_evidence_by_phone = {PHONE: [{"clock": "11:02",
                                                   "evidence": ["Sprachnachricht", "0:07"]}]}
    assert rig.executor.auto_match_media() == {"attached": 1, "weak": 0}
    [event] = [e for e in rig.executor.outbox()["events"] if e["payload"].get("media_id")]
    assert event["payload"]["media_link_strength"] == "strong"


def test_a_legacy_row_never_wins_a_live_candidate_over_the_fresh_file(rig):
    """TASK-131 round 6 blocker B3, fixed: a pre-round-5 backfilled row (``legacy=1``) must not
    compete for a live candidate at all -- it stays queued (still reachable by a human via
    ``unresolved_media``/``attach_media``) while the fresh file of the same kind gets the match."""
    old = _seed_media(rig, kind="image", size=999, filename="IMG-OLD.jpg", media_id="wab.m.old")
    rig.ledger._db.execute("update media_seen set legacy=1 where media_id=?", (old["media_id"],))
    rig.ledger._db.commit()
    _seed_media(rig, kind="image", size=1000, filename="IMG-NEW.jpg", media_id="wab.m.new")
    rig.executor.record_inbound(_inbound(media="image", phone=PHONE, clock="10:04"), [], rig.clock())

    assert rig.executor.auto_match_media() == {"attached": 1, "weak": 0}
    [event] = [e for e in rig.executor.outbox()["events"] if e["payload"].get("media_id")]
    assert event["payload"]["media_id"] == "wab.m.new"
    # the legacy row is untouched, still in the queue, still visible to a human
    remaining = [r["media_id"] for r in rig.ledger.media_queue()]
    assert remaining == ["wab.m.old"]


def test_two_images_with_nothing_to_distinguish_them_attach_weak_not_stalled(rig):
    """Ivan's own example: an image bubble shows neither size nor a name. Attached anyway --
    deterministically -- and marked weak, visible on the row and in the backlog, never a stall."""
    _seed_media(rig, kind="image", size=1000, filename="IMG-1.jpg")
    rig.executor.record_inbound(_inbound(media="image", phone=PHONE, clock="10:04"), [], rig.clock())
    rig.executor.record_inbound(_inbound(media="image", phone=OTHER, clock="10:04"), [], rig.clock())

    assert rig.executor.auto_match_media() == {"attached": 1, "weak": 1}
    assert rig.ledger.media_backlog(rig.clock())["weak_links"] == 1
    [event] = [e for e in rig.executor.outbox()["events"] if e["payload"].get("media_id")]
    assert event["payload"]["media_link_strength"] == "weak"


def test_zero_same_kind_candidates_stays_queued_not_guessed(rig):
    _seed_media(rig, kind="document", size=10, filename="x.pdf")
    assert rig.executor.auto_match_media() == {"attached": 0, "weak": 0}
    assert len(rig.ledger.media_queue()) == 1


def test_an_unreadable_candidate_thread_does_not_block_a_readable_one(rig):
    """One candidate's chat will not open (a stale contact, a driver hiccup); the sweep still
    reaches a decision using the other -- and the row is not stranded for next cycle to retry blind."""
    _seed_media(rig, kind="document", size=165000, filename="Anna.pdf")
    rig.executor.record_inbound(_inbound(media="document", phone=PHONE, clock="10:04"), [], rig.clock())
    rig.executor.record_inbound(_inbound(media="document", phone=OTHER, clock="10:04"), [], rig.clock())
    rig.driver.fail_on_open = "no chat on the handset for this number"
    rig.driver.chats = {}  # unused; fail_on_open fires on any open_chat call

    result = rig.executor.auto_match_media()
    assert result["attached"] == 1  # weak tie-break: neither candidate's evidence was readable


# --- active-hours override for a single test run (TASK-131 UAT, Ivan 2026-09-22) -------------------
def test_active_hours_override_is_none_when_unset_or_blank():
    """The default path -- what every deploy without the env var set gets -- must be bit-for-bit
    unchanged: None, so main() keeps G.MINI_FLOOR exactly as built."""
    assert S.active_hours_override(None) is None
    assert S.active_hours_override("") is None
    assert S.active_hours_override("   ") is None


def test_active_hours_override_parses_lo_hi():
    assert S.active_hours_override("9-23") == (9, 23)
    assert S.active_hours_override("0-24") == (0, 24)


def test_active_hours_override_rejects_garbage_and_out_of_range():
    for bad in ("garbage", "9", "23-9", "9-9", "-1-23", "9-25"):
        with pytest.raises(RuntimeError):
            S.active_hours_override(bad)


def test_health_says_the_msisdn_is_unverified(rig):
    health = rig.executor.health()
    assert health["rail"]["msisdn_verified"] is False
    assert "TASK-136" in health["rail"]["note"]
    assert health["quota"]["window_open"] is True
    assert health["quota"]["active_hours"] == [9, 20]


def test_retention_sweeps_ship_with_the_first_commit(rig):
    rig.send()
    old = rig.clock.now + timedelta(days=L.LEDGER_RETENTION_DAYS + 1)
    swept = S.maintenance_once(rig.executor, now=old)
    assert swept["ledger"]["outbound"] == 1 and swept["ledger"]["journal"] >= 1


# --- the HTTP surface -------------------------------------------------------------------------------------
@pytest.fixture
def http(tmp_path):
    rig = Rig(tmp_path)
    server = S.BridgeServer(rig.executor, "s3cret", port=0, log=lambda *a: None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield rig, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    rig.ledger.close()


def call(base, path, *, token="s3cret", payload=None):
    req = urllib.request.Request(base + path, method="POST" if payload is not None else "GET",
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_the_wire_refuses_a_bad_token_before_anything_else(http):
    rig, base = http
    status, body = call(base, "/v1/health", token="wrong")
    assert (status, body["error"]["code"]) == (401, "unauthorized")
    assert rig.driver.lock_events == []


def test_the_four_routes_answer_on_loopback(http):
    rig, base = http
    assert call(base, "/v1/health")[1]["version"] == X.VERSION
    status, body = call(base, "/v1/messages", payload={
        "client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "Guten Tag",
        "trace": {"action": "reply"}})
    assert (status, body["state"]) == (200, "sent")
    assert call(base, "/v1/outbox")[1]["events"] == []
    assert call(base, "/v1/reconcile", payload={"client_msg_ids": [KEY]})[1]["results"][0][
        "verdict"] == "confirmed_sent"
    assert call(base, "/v1/nope", payload={})[0] == 400


def test_a_refusal_travels_as_the_contract_envelope(http):
    rig, base = http
    rig.driver.ticks = [D.UNVERIFIED]
    status, body = call(base, "/v1/messages", payload={
        "client_msg_id": KEY, "to": PHONE, "kind": "text", "body": "Guten Tag",
        "trace": {"action": "reply"}})
    assert status == 504
    assert body["error"]["code"] == "send_unconfirmed"
    assert body["error"]["retryable"] is False


def test_v1_photos_answers_200_on_a_real_send(http, tmp_path):
    """Regression, found live 2026-09-23 while wiring /v1/gallery next to this route: the handler
    used to hand executor.send_photos' plain 3-key dict straight to _dispatch, which does
    ``status, payload = route()`` -- unpacking a dict that way raises ValueError, caught by
    _dispatch's own generic handler as a false 500. Every live send attempted through this route
    before the bug was found had hit a BridgeRefusal first (which raises before the assignment
    ever runs), so a genuinely successful send had never once reached this line."""
    rig, base = http
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    status, body = call(base, "/v1/photos", payload={"phone": PHONE, "local_paths": [str(a)]})
    assert status == 200
    assert body["ok"] is True
    assert body["sent"] == [{"clock": "09:15", "tick": "Gesendet"}]


def test_v1_gallery_answers_200_on_a_real_send(http, tmp_path):
    rig, base = http
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    status, body = call(base, "/v1/gallery", payload={
        "phone": PHONE, "local_paths": [str(a)], "caption": "Unsere Klinik"})
    assert status == 200
    assert body["ok"] is True
    assert (body["clock"], body["tick"]) == ("09:15", "Gesendet")


def test_v1_document_answers_200_on_a_real_send(http, tmp_path):
    rig, base = http
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    status, body = call(base, "/v1/document", payload={
        "phone": PHONE, "local_path": str(a), "caption": "Bitte pruefen"})
    assert status == 200
    assert body["ok"] is True
    assert (body["clock"], body["tick"]) == ("09:15", "Gesendet")


# --- the inbound watcher (TASK-143, lock removed TASK-131 round 6) --------------------------------
class BusyPhone(D.FakeDriver):
    """The other lane holds huawei01.lock for a send. The shade read must not care."""

    def lock(self, *, timeout=D.LOCK_TIMEOUT_SEC):
        raise D.PhoneBusy(f"phone lock busy for {timeout:.0f}s: /home/x/huawei01.lock")


class BrokenPhone(D.FakeDriver):
    """adb itself is gone -- the one failure the shade read can still have."""

    def pull_inbound(self):
        raise D.DriverError("L2N4C19B14054874 is not in adb 'device' state")


def test_a_watcher_cycle_stores_what_the_phone_saw_and_beats(rig):
    rig.driver.inbound = _inbound()
    watch = W.InboundWatcher(rig.executor, log=lambda _m: None)
    assert watch.cycle() == {"stored": 1, "seen": 1, "unresolved": 0}
    assert watch.heartbeat()["last_ok_at"] == L.utc(rig.clock())
    assert watch.errors == 0


def test_a_notification_arriving_while_the_lock_is_held_is_still_queued_and_processed(rig):
    """THE ACCEPTANCE CASE (TASK-131 round 6): the shade read takes no lock at all, so a send in
    flight (or anything else holding huawei01.lock) can never make this watcher skip a cycle -- the
    root of half the decoy attributions this round's brief was written from."""
    driver = BusyPhone(inbound=_inbound())
    rig.executor.driver = driver
    watch = W.InboundWatcher(rig.executor, log=lambda _m: None)
    assert watch.cycle() == {"stored": 1, "seen": 1, "unresolved": 0}
    assert watch.errors == 0 and watch.last_ok_at == L.utc(rig.clock())
    assert rig.executor.outbox()["events"], "the notification reached the durable queue"
    assert not hasattr(watch, "busy_cycles"), "there is nothing left for this counter to count"


def test_a_phone_that_is_gone_is_counted_and_journalled_and_never_raises(rig):
    rig.executor.driver = BrokenPhone()
    watch = W.InboundWatcher(rig.executor, log=lambda _m: None)
    assert watch.cycle() is None
    assert watch.errors == 1
    assert "adb 'device' state" in watch.last_error
    assert watch.heartbeat()["last_ok_at"] is None


# --- the identity watcher (TASK-131 round 6) --------------------------------------------------------
def test_an_identity_watcher_cycle_runs_the_sweep_and_beats(rig):
    _seed_media(rig, kind="image", size=1000, filename="IMG-1.jpg")
    rig.executor.record_inbound(_inbound(media="image", phone=PHONE), [], rig.clock())
    watch = W.IdentityWatcher(rig.executor, log=lambda _m: None)

    assert watch.cycle() == {"attached": 1, "weak": 0}
    assert watch.heartbeat()["last_ok_at"] == L.utc(rig.clock())
    assert (watch.attached_total, watch.weak_total, watch.errors) == (1, 0, 0)


def test_an_identity_watcher_error_is_counted_and_journalled_and_never_raises(rig):
    class BrokenExecutor:
        clock = rig.clock
        ledger = rig.ledger

        def auto_match_media(self):
            raise RuntimeError("boom")

    watch = W.IdentityWatcher(BrokenExecutor(), log=lambda _m: None)
    assert watch.cycle() is None
    assert watch.errors == 1 and "boom" in watch.last_error
    assert watch.heartbeat()["last_ok_at"] is None


def test_health_reports_the_identity_watcher(rig):
    W.IdentityWatcher(rig.executor, log=lambda _m: None).start()
    heartbeat = rig.executor.health()["identity_watcher"]
    assert heartbeat["alive"] is True
    heartbeat_direct = rig.executor.identity_watcher
    heartbeat_direct.stop()


def test_health_reports_no_identity_watcher_when_none_is_wired(rig):
    assert rig.executor.health()["identity_watcher"] is None


# --- POST /v1/photos: outbound media (TASK-131 round 7, Ivan 2026-09-22) ------------------------
def test_send_photos_opens_the_chat_sends_each_file_and_parks(rig, tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"a"); b.write_bytes(b"b")
    result = rig.executor.send_photos(PHONE, [str(a), str(b)])
    assert result["ok"] is True
    assert result["sent"] == [{"clock": "09:15", "tick": "Gesendet"}] * 2
    assert rig.driver.opened == [PHONE]
    assert rig.driver.sent_photos == [(PHONE, str(a)), (PHONE, str(b))]
    assert rig.driver.parked == 1, "the phone is released after the last photo, not held"


def test_send_photos_refuses_a_missing_file_before_touching_the_phone(rig, tmp_path):
    real = tmp_path / "a.jpg"
    real.write_bytes(b"a")
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_photos(PHONE, [str(real), str(tmp_path / "missing.jpg")])
    assert caught.value.code == "invalid_request"
    assert "do not exist" in str(caught.value)
    assert rig.driver.opened == [], "refused before the phone lock was even taken"


def test_send_photos_refuses_more_than_the_cap_before_touching_the_phone(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_photos(PHONE, [f"/tmp/{i}.jpg" for i in range(D.MAX_PHOTOS_PER_SEND + 1)])
    assert "at most" in str(caught.value)
    assert rig.driver.opened == [], "refused before the phone lock was even taken"


def test_send_photos_refuses_an_empty_list(rig):
    with pytest.raises(E.BridgeRefusal):
        rig.executor.send_photos(PHONE, [])


def test_a_send_photo_failure_partway_through_reports_how_many_actually_sent(rig, tmp_path):
    """The first photo lands, the second raises -- the caller has to know one real send already
    reached the candidate, not just that the call as a whole failed (TASK-146's own discipline for
    text: keys pressed are keys pressed, never silently retried)."""
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"a"); b.write_bytes(b"b")
    rig.driver.ticks = ["Gesendet"]

    def _fail_second(driver):
        if len(driver.sent_photos) == 1:
            driver.fail_on_send_photo = "boom"
    rig.driver.hook = _fail_second
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_photos(PHONE, [str(a), str(b)])
    assert caught.value.code == "send_unconfirmed", (
        "a real photo already reached the handset -- this must stay the 'handset was touched' 504, "
        "never the pre-flight 400")
    assert "sent 1/2 photos" in str(caught.value)
    assert rig.driver.sent_photos == [(PHONE, str(a)), (PHONE, str(b))], (
        "the second attempt still reached the driver -- keys/taps may already have happened")


# --- one gallery message (TASK-131 round 7 gallery redesign, Ivan 2026-09-22) -------------------
def test_send_gallery_opens_the_chat_sends_one_message_and_parks(rig, tmp_path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"a"); b.write_bytes(b"b")
    result = rig.executor.send_gallery(PHONE, [str(a), str(b)], caption="Unsere Klinik")
    assert result["ok"] is True
    assert (result["clock"], result["tick"]) == ("09:15", "Gesendet")
    assert rig.driver.opened == [PHONE]
    assert rig.driver.sent_galleries == [(PHONE, [str(a), str(b)], "Unsere Klinik")]
    assert rig.driver.parked == 1


def test_send_gallery_defaults_to_no_caption(rig, tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    rig.executor.send_gallery(PHONE, [str(a)])
    assert rig.driver.sent_galleries == [(PHONE, [str(a)], "")]


def test_send_gallery_refuses_a_missing_file_before_touching_the_phone(rig, tmp_path):
    real = tmp_path / "a.jpg"
    real.write_bytes(b"a")
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_gallery(PHONE, [str(real), str(tmp_path / "missing.jpg")])
    assert caught.value.code == "invalid_request"
    assert "do not exist" in str(caught.value)
    assert rig.driver.opened == [], "refused before the phone lock was even taken"


def test_send_gallery_refuses_more_than_the_cap_before_touching_the_phone(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_gallery(PHONE, [f"/tmp/{i}.jpg" for i in range(D.MAX_PHOTOS_PER_SEND + 1)])
    assert "at most" in str(caught.value)
    assert rig.driver.opened == [], "refused before the phone lock was even taken"


def test_send_gallery_refuses_an_empty_list(rig):
    with pytest.raises(E.BridgeRefusal):
        rig.executor.send_gallery(PHONE, [])


def test_a_send_gallery_failure_is_reported_as_handset_touched_not_a_refusal(rig, tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    rig.driver.fail_on_send_gallery = "boom"
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_gallery(PHONE, [str(a)])
    assert caught.value.code == "send_unconfirmed"
    assert "boom" in str(caught.value)
    assert rig.driver.sent_galleries == [(PHONE, [str(a)], "")], (
        "the attempt still reached the driver -- keys/taps may already have happened")


# --- one document (TASK-131 round 7, Ivan 2026-09-23) -------------------------------------------
def test_send_document_opens_the_chat_sends_and_parks(rig, tmp_path):
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    result = rig.executor.send_document(PHONE, str(a), caption="Bitte pruefen")
    assert result["ok"] is True
    assert (result["clock"], result["tick"]) == ("09:15", "Gesendet")
    assert rig.driver.opened == [PHONE]
    assert rig.driver.sent_documents == [(PHONE, str(a), "Bitte pruefen")]
    assert rig.driver.parked == 1


def test_send_document_defaults_to_no_caption(rig, tmp_path):
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    rig.executor.send_document(PHONE, str(a))
    assert rig.driver.sent_documents == [(PHONE, str(a), "")]


def test_send_document_refuses_a_missing_file_before_touching_the_phone(rig, tmp_path):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_document(PHONE, str(tmp_path / "missing.pdf"))
    assert caught.value.code == "invalid_request"
    assert "does not exist" in str(caught.value)
    assert rig.driver.opened == [], "refused before the phone lock was even taken"


def test_send_document_refuses_an_empty_path(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_document(PHONE, "")
    assert caught.value.code == "invalid_request"
    assert rig.driver.opened == []


def test_a_send_document_failure_is_reported_as_handset_touched_not_a_refusal(rig, tmp_path):
    a = tmp_path / "Lebenslauf.pdf"
    a.write_bytes(b"a")
    rig.driver.fail_on_send_document = "boom"
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.executor.send_document(PHONE, str(a))
    assert caught.value.code == "send_unconfirmed"
    assert "boom" in str(caught.value)
    assert rig.driver.sent_documents == [(PHONE, str(a), "")], (
        "the attempt still reached the driver -- keys/taps may already have happened")
