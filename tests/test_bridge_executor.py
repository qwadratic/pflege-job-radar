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
from bridge import errors as E
from bridge import executor as X
from bridge import governor as G
from bridge import inbound as I
from bridge import ledger as L
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
def _inbound(text="Ja, gerne", phone=PHONE, clock="10:04"):
    return I.assign_ids([I.InboundMessage(counterparty=phone, title=phone, text=text,
                                          local_date="2026-09-23", clock=clock,
                                          source="notification", time_ms=1789312500000)])


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


# --- the inbound watcher (TASK-143) --------------------------------------------------------------
class BusyPhone(D.FakeDriver):
    """The other lane is holding the flock. That is the expected case, not an incident."""

    def lock(self, *, timeout=D.LOCK_TIMEOUT_SEC):
        raise D.PhoneBusy(f"phone lock busy for {timeout:.0f}s: /home/x/huawei01.lock")


class BrokenPhone(D.FakeDriver):
    def lock(self, *, timeout=D.LOCK_TIMEOUT_SEC):
        raise D.DriverError("L2N4C19B14054874 is not in adb 'device' state")


def test_a_watcher_cycle_stores_what_the_phone_saw_and_beats(rig):
    rig.driver.inbound = _inbound()
    watch = W.InboundWatcher(rig.executor, log=lambda _m: None)
    assert watch.cycle() == {"stored": 1, "seen": 1, "unresolved": 0}
    assert watch.heartbeat()["last_ok_at"] == L.utc(rig.clock())
    assert watch.errors == 0


def test_a_busy_flock_is_a_skipped_cycle_and_not_an_error(rig):
    rig.executor.driver = BusyPhone()
    watch = W.InboundWatcher(rig.executor, log=lambda _m: None)
    assert watch.cycle() is None
    assert (watch.busy_cycles, watch.errors, watch.last_ok_at) == (1, 0, None)


def test_a_phone_that_is_gone_is_counted_and_journalled_and_never_raises(rig):
    rig.executor.driver = BrokenPhone()
    watch = W.InboundWatcher(rig.executor, log=lambda _m: None)
    assert watch.cycle() is None
    assert (watch.errors, watch.busy_cycles) == (1, 0)
    assert "adb 'device' state" in watch.last_error
    assert watch.heartbeat()["last_ok_at"] is None
