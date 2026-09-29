"""Offline proof for the handset operations (TASK-376). FakeDriver only -- no adb, no phone.

What each test here is really about is a thing that would otherwise be found out on the handset:
a broadcast that re-sends a delivered recipient after a restart, a run that one bad number ends.
"""
import copy
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bridge import broadcast as B
from bridge import driver as D
from bridge import errors as E
from bridge import executor as X
from bridge import governor as G
from bridge import inbound as I
from bridge import ledger as L
from bridge import operations as O
from bridge import server as S

BERLIN = ZoneInfo("Europe/Berlin")
IVAN = "+491700000001"
PARTNER = "+491700000002"
SOAK = "+491700000003"
KEYS = ["wab.o.%032d" % n for n in range(1, 9)]


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


def bubbles(*spec):
    """('in', 'Hallo', '09:10'), ('out', 'Guten Tag', '09:11', 'Gelesen') -> [BubbleView]."""
    out = []
    for item in spec:
        direction, text, clock = item[0], item[1], item[2]
        tick = item[3] if len(item) > 3 else ("Gelesen" if direction == "out" else "")
        out.append(D.BubbleView(direction, text, clock, tick))
    return out


CHATS = {
    "Ivan Test": {"phone": IVAN, "unread": 1, "stamp": "13:36", "archived": False,
                  "bubbles": bubbles(("in", "Hallo", "13:30"), ("out", "Guten Tag", "13:36"))},
    "+49 170 0000002": {"phone": PARTNER, "unread": 11, "stamp": "09:10", "archived": False,
                        "bubbles": bubbles(("in", "Test 1", "09:05"), ("in", "Test 2", "09:10"))},
    "Soak Rail": {"phone": SOAK, "unread": 0, "stamp": "18.08.26", "archived": False,
                  "bubbles": bubbles(("out", "soak", "07:02", "Zugestellt"))},
}


class Rig:
    """ledger + governor + fake phone + executor + operations + broadcast, on one tmp sqlite."""

    def __init__(self, tmp_path, *, moment=None, per_number_cap=3, chats=None):
        self.clock = Clock(moment or berlin(2026, 9, 23, 10, 0))   # a Wednesday, inside 9-20
        self.ledger = L.Ledger(tmp_path / "ledger.sqlite")
        # deepcopy, not dict(): the bubble lists are mutated by a send, and a shallow copy would
        # let one test's message land in the next test's chat.
        self.driver = D.FakeDriver(chats=copy.deepcopy(chats or CHATS))
        self.governor = G.Governor(self.ledger, per_number_daily_cap=per_number_cap,
                                   rng=__import__("random").Random(7))
        self.executor = X.Executor(ledger=self.ledger, governor=self.governor, driver=self.driver,
                                   rail_number=None, clock=self.clock, tick_wait_sec=0.0,
                                   sleep=lambda _s: None, monotonic=_fake_monotonic())
        self.ops = O.Operations(self.executor)
        self.broadcast = B.Broadcast(self.executor)

    def items(self, *pairs, action="reply"):
        return [{"client_msg_id": KEYS[i], "to": phone, "body": body, "action": action}
                for i, (phone, body) in enumerate(pairs)]


def _fake_monotonic():
    ticks = iter([0.0] + [10_000.0] * 10_000)
    return lambda: next(ticks)


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.ledger.close()


# --- list_chats -------------------------------------------------------------------------------
def test_the_chat_list_names_every_chat_and_who_it_belongs_to(rig):
    result = rig.ops.list_chats()
    assert result["count"] == 3
    by_title = {c["title"]: c for c in result["chats"]}
    assert by_title["Ivan Test"]["phone"] == IVAN
    assert by_title["Ivan Test"]["phone_source"] == "address_book"
    assert by_title["Ivan Test"]["unread"] == 1
    # An unsaved contact's row IS its number, and that needs no address book at all.
    assert by_title["+49 170 0000002"]["phone_source"] == "title_is_number"
    assert by_title["+49 170 0000002"]["phone"] == PARTNER


def test_a_listing_never_carries_a_message_body(rig):
    """The last-message line is a message. A listing call says one exists, never what it says."""
    blob = json.dumps(rig.ops.list_chats())
    for chat in CHATS.values():
        for bubble in chat["bubbles"]:
            assert bubble.text not in blob
    assert all(c["has_preview"] is True for c in rig.ops.list_chats()["chats"])


def test_a_display_name_two_contacts_share_is_reported_and_not_resolved(rig):
    rig.driver.ambiguous_titles = ["Ivan Test"]
    row = [c for c in rig.ops.list_chats()["chats"] if c["title"] == "Ivan Test"][0]
    assert (row["ambiguous"], row["phone"]) == (True, None)


def test_listing_the_chats_takes_the_phone_once_and_parks(rig):
    rig.ops.list_chats()
    assert rig.driver.lock_events == ["acquire", "release"]
    assert rig.driver.parked == 1


# --- reconcile_unread (TASK-234) ------------------------------------------------------------
def test_reconcile_unread_reads_every_unread_chat_nobody_else_touched(rig):
    """The third door: a chat nobody sends to, reads from or attaches media for gets no
    piggyback read at all (TASK-231's fix only ever fires when some OTHER operation already had
    its own reason to open that exact chat), and if the shade also missed the message (full
    shade, revoked notification access, a reboot before a poll) it sits uncaptured until a human
    notices the unread badge on the handset. reconcile_unread reads every unread row back, the
    same read_thread already does for any other caller -- CHATS has two: 'Ivan Test' (unread=1)
    and '+49 170 0000002' (unread=11); 'Soak Rail' (unread=0) is left alone."""
    fresh = I.assign_ids([I.InboundMessage(counterparty=IVAN, title=IVAN, text="Bin gleich da",
                                           local_date="2026-09-23", clock="13:40",
                                           source="thread")])
    rig.driver.cold_thread = fresh
    result = rig.ops.reconcile_unread()
    assert (result["chats"], result["reconciled"], result["skipped"]) == (3, 2, 0)
    events = rig.executor.outbox()["events"]
    assert [e["payload"]["text"] for e in events] == ["Bin gleich da"]


def test_reconcile_unread_skips_a_row_it_cannot_prove_the_identity_of(rig):
    """Same refusal ``_identify`` already makes for every other caller of list_chats -- guessing
    which conversation an ambiguous display name belongs to is worse than not reading it."""
    rig.driver.ambiguous_titles = ["Ivan Test"]        # unread=1, now unresolvable
    result = rig.ops.reconcile_unread()
    # '+49 170 0000002' (unread=11) is still unambiguous and gets reconciled.
    assert (result["reconciled"], result["skipped"]) == (1, 1)
    rows = journal(rig, "reconcile_skipped")
    assert len(rows) == 1
    assert json.loads(rows[0]["detail"])["reason"] == "ambiguous_display_name"


def test_reconcile_unread_never_raises_on_a_row_that_will_not_open(rig):
    """One bad row does not end the sweep -- the same isolation ``_read_evidence_for`` already
    keeps for its own per-candidate opens."""
    real_open = rig.driver.open_chat

    def flaky_open(phone):
        if phone == IVAN:
            raise D.DriverError("conversation did not open (scripted)")
        return real_open(phone)

    rig.driver.open_chat = flaky_open
    result = rig.ops.reconcile_unread()
    assert (result["reconciled"], result["skipped"]) == (1, 0)
    assert journal(rig, "reconcile_read_failed")


# --- read_thread ------------------------------------------------------------------------------
def test_reading_a_thread_reports_direction_clock_and_tick(rig):
    result = rig.ops.read_thread(phone=IVAN)
    assert [m["direction"] for m in result["messages"]] == ["in", "out"]
    assert result["messages"][1]["clock"] == "13:36"
    assert result["messages"][1]["tick_state"] == "read"
    assert (result["incoming"], result["outgoing"]) == (1, 1)
    assert result["visibility"] == O.VISIBILITY


def test_a_thread_can_be_read_without_its_text(rig):
    result = rig.ops.read_thread(phone=IVAN, include_text=False)
    assert "body" not in result["messages"][0]
    assert result["messages"][0]["body_sha256"] == D.body_sha256("Hallo")


def test_a_reply_that_arrives_while_the_chat_is_open_for_read_thread_is_still_recorded(rig):
    """TASK-231: opening a chat clears its notification and WhatsApp posts no shade record while
    it is foregrounded -- so a message that lands during this read has exactly one door left, the
    piggyback read_thread now takes before it parks. Injected from ``read_hook``, which fires
    inside ``read_bubbles()``, so this really is 'arrived while the chat was open', not 'was
    already there when it opened'."""
    fresh = I.assign_ids([I.InboundMessage(counterparty=IVAN, title=IVAN, text="Bin gleich da",
                                           local_date="2026-09-23", clock="13:40",
                                           source="thread")])

    def inject(drv):
        drv.cold_thread.extend(fresh)

    rig.driver.read_hook = inject
    rig.ops.read_thread(phone=IVAN)

    events = rig.executor.outbox()["events"]
    assert [e["payload"]["text"] for e in events] == ["Bin gleich da"]


def test_a_chat_opened_by_title_alone_has_no_number_to_mint_an_inbound_id_against(rig):
    """The same piggyback would need to mint an id with no counterparty (bridge/inbound.py::mint
    raises on that) -- read_thread must not even try when only a title was given."""
    rig.driver.cold_thread = [object()]   # would blow up if read_cold_thread were ever called
    rig.ops.read_thread(chat="Ivan Test")
    assert rig.executor.outbox()["events"] == []


def test_naming_both_a_number_and_a_title_is_refused(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.read_thread(phone=IVAN, chat="Ivan Test")
    assert caught.value.status_code == 400


# --- send_message -----------------------------------------------------------------------------
def test_send_message_is_the_executor_called_as_a_function(rig):
    result = rig.ops.send_message(to=IVAN, body="Guten Tag", client_msg_id=KEYS[0], action="reply")
    assert result["state"] == "sent"
    assert result["verified"]["tick_state"] == "sent"
    assert rig.ledger.get(KEYS[0]).state == L.SENT
    assert rig.driver.sent == ["Guten Tag"]
    assert rig.driver.lock_events == ["acquire", "release"]   # one bubble, one acquisition


def test_send_message_keeps_the_no_tick_no_sent_rule(rig):
    rig.driver.ticks = [""]          # the bubble is drawn and never acknowledged
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.send_message(to=IVAN, body="Guten Tag", client_msg_id=KEYS[0], action="reply")
    assert (caught.value.code, caught.value.status_code) == ("send_unconfirmed", 504)
    assert rig.ledger.get(KEYS[0]).state == L.UNCONFIRMED


# --- send_broadcast ---------------------------------------------------------------------------
def test_a_new_run_queues_everything_and_sends_nothing(rig):
    view = rig.broadcast.create({"run_id": "probe-1", "note": "three test numbers",
                                 "items": rig.items((IVAN, "eins"), (PARTNER, "zwei"))})
    assert view["counts"] == {"queued": 2}
    assert view["run"]["state"] == "open"
    assert rig.driver.sent == []          # creating a run touches no phone


def test_the_runner_sends_one_item_per_step_and_records_each(rig):
    rig.broadcast.create({"run_id": "probe-1",
                          "items": rig.items((IVAN, "eins"), (PARTNER, "zwei"))})
    assert rig.broadcast.step()["status"] == L.ITEM_SENT
    rig.clock.advance(30)
    assert rig.broadcast.step()["status"] == L.ITEM_SENT
    assert rig.broadcast.step() is None                # nothing left to do
    view = rig.broadcast.view("probe-1")
    assert view["counts"] == {"sent": 2}
    assert view["run"]["state"] == "done"
    assert rig.driver.sent == ["eins", "zwei"]


def test_a_queued_phone_op_defers_the_step_instead_of_racing_it(rig):
    """TASK-268: this runner is not routed through bridge/dispatcher.py, so it would otherwise race
    a dispatched op (a candidate-facing reply, patience driver.LOCK_TIMEOUT_SEC=30s) for
    huawei01.lock across one bubble's own 90-150s hold. Fails before the fix (the item is attempted,
    the phone is opened); passes after it (the item is left exactly as due, untouched)."""
    rig.broadcast.create({"run_id": "probe-1", "items": rig.items((IVAN, "eins"))})
    rig.ledger.enqueue_op("op.reply", "send", {"req": {"to": PARTNER}}, rig.clock())

    assert rig.broadcast.step() is None
    assert rig.driver.lock_events == [], "a queued phone op must not be raced for the flock"
    assert rig.broadcast.view("probe-1")["counts"] == {"queued": 1}


def test_a_broadcast_resumes_after_a_crash_without_re_sending_a_delivered_item(rig):
    """The crash window: the bubble went out and the process died before the item was updated.

    The item is still ``queued``, so the runner picks it up again -- and the second attempt carries
    the same deterministic key, which first-body-wins answers with the original result. The proof
    is that the handset was not typed at a second time.
    """
    rig.broadcast.create({"run_id": "probe-1",
                          "items": rig.items((IVAN, "eins"), (PARTNER, "zwei"))})
    rig.broadcast.step()
    assert rig.driver.sent == ["eins"]
    # The crash: the outbound ledger row says sent, the broadcast item never heard about it.
    rig.ledger.mark_item("probe-1", KEYS[0], L.ITEM_QUEUED, rig.clock())
    rig.clock.advance(30)

    result = rig.broadcast.step()

    assert (result["client_msg_id"], result["status"]) == (KEYS[0], L.ITEM_SENT)
    assert rig.driver.sent == ["eins"]                 # NOT typed twice
    item = [i for i in rig.broadcast.view("probe-1")["items"] if i["client_msg_id"] == KEYS[0]][0]
    assert item["detail"] == "replayed"


def test_a_restart_loses_nothing_because_the_run_is_in_the_ledger(tmp_path):
    """A fresh Broadcast over the same ledger file is what a service restart looks like."""
    first = Rig(tmp_path)
    first.broadcast.create({"run_id": "probe-1",
                            "items": first.items((IVAN, "eins"), (PARTNER, "zwei"))})
    first.broadcast.step()
    first.ledger.close()

    second = Rig(tmp_path)
    second.clock.now = first.clock.now + timedelta(seconds=30)
    assert second.broadcast.view("probe-1")["counts"] == {"sent": 1, "queued": 1}
    assert second.broadcast.step()["status"] == L.ITEM_SENT
    assert second.driver.sent == ["zwei"]              # only the item that was still owed
    second.ledger.close()


def test_one_refused_recipient_does_not_end_the_run(rig):
    rig.broadcast.create({"run_id": "probe-1", "items": rig.items(
        (IVAN, "eins"), ("+491700000999", "zwei"), (PARTNER, "drei"))})
    rig.driver.fail_on_open = "conversation did not open"

    first = rig.broadcast.step()                       # the chat will not open for anyone now
    assert (first["status"], first["code"]) == (L.ITEM_REFUSED, "not_on_whatsapp")
    rig.driver.fail_on_open = None
    rig.clock.advance(30)

    assert rig.broadcast.step()["status"] == L.ITEM_REFUSED   # the number that is not on WhatsApp
    rig.clock.advance(30)
    assert rig.broadcast.step()["status"] == L.ITEM_SENT      # and the run carried on regardless
    view = rig.broadcast.view("probe-1")
    assert view["counts"] == {"refused": 2, "sent": 1}
    assert view["run"]["state"] == "done"


def test_a_governor_refusal_defers_the_item_instead_of_failing_it(rig):
    """Two bubbles to one recipient inside the 4 s floor: paced, not lost."""
    rig.broadcast.create({"run_id": "probe-1", "items": rig.items((IVAN, "eins"), (IVAN, "zwei"))})
    assert rig.broadcast.step()["status"] == L.ITEM_SENT

    result = rig.broadcast.step()

    assert (result["status"], result["code"]) == (L.ITEM_QUEUED, "rail_parked")
    assert result["next_attempt_at"] > L.utc(rig.clock.now)
    assert rig.broadcast.step() is None                # not due yet: it does not spin
    assert rig.broadcast.view("probe-1")["run"]["state"] == "open"
    rig.clock.advance(600)
    assert rig.broadcast.step()["status"] == L.ITEM_SENT


def test_quiet_hours_park_a_run_rather_than_dropping_it(tmp_path):
    rig = Rig(tmp_path, moment=berlin(2026, 9, 23, 3, 0))    # 03:00, outside 9-20
    rig.broadcast.create({"run_id": "probe-1", "items": rig.items((IVAN, "eins"))})
    result = rig.broadcast.step()
    assert (result["status"], result["code"]) == (L.ITEM_QUEUED, "rail_parked")
    assert rig.driver.sent == []
    rig.ledger.close()


def test_the_hard_stop_stops_the_run_and_keeps_the_rest_queued(rig):
    rig.broadcast.create({"run_id": "probe-1", "items": rig.items(
        (IVAN, "eins"), (PARTNER, "zwei"), (SOAK, "drei"))})
    rig.broadcast.step()

    view = rig.broadcast.stop("probe-1")

    assert view["run"]["state"] == "stopped" and view["run"]["stop_requested"] is True
    assert view["counts"] == {"sent": 1, "queued": 2}
    rig.clock.advance(600)
    assert rig.broadcast.step() is None                # the runner does not touch a stopped run
    assert rig.driver.sent == ["eins"]


def test_a_stop_survives_a_restart(tmp_path):
    first = Rig(tmp_path)
    first.broadcast.create({"run_id": "probe-1", "items": first.items((IVAN, "eins"))})
    first.broadcast.stop("probe-1")
    first.ledger.close()

    second = Rig(tmp_path)
    assert second.broadcast.step() is None
    assert second.driver.sent == []
    second.ledger.close()


def test_a_run_refuses_two_items_under_one_key(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.broadcast.create({"run_id": "probe-1", "items": [
            {"client_msg_id": KEYS[0], "to": IVAN, "body": "eins", "action": "reply"},
            {"client_msg_id": KEYS[0], "to": PARTNER, "body": "zwei", "action": "reply"}]})
    assert caught.value.status_code == 400
    assert rig.ledger.get_run("probe-1") is None       # nothing was written


def test_a_bad_item_rejects_the_whole_run_rather_than_part_of_it(rig):
    with pytest.raises(E.BridgeRefusal):
        rig.broadcast.create({"run_id": "probe-1", "items": [
            {"client_msg_id": KEYS[0], "to": IVAN, "body": "eins", "action": "reply"},
            {"client_msg_id": KEYS[1], "to": "0170 nope", "body": "zwei", "action": "reply"}]})
    assert rig.ledger.get_run("probe-1") is None
    assert rig.ledger.get(KEYS[0]) is None


def test_the_broadcast_view_reports_status_per_item_without_the_bodies(rig):
    rig.broadcast.create({"run_id": "probe-1", "items": rig.items((IVAN, "geheim"))})
    view = rig.broadcast.view("probe-1")
    assert "geheim" not in json.dumps(view)
    assert view["items"][0]["body_sha256"] == D.body_sha256("geheim")
    assert view["items"][0]["status"] == L.ITEM_QUEUED


def test_a_pacing_value_the_governor_cannot_read_is_refused_before_the_run_is_stored(rig):
    """The governor reads min_gap_ms with float(). A null got as far as 200 and then raised on
    every poll of an item that stayed queued -- and next_due_item hands the oldest run's first
    queued item back every time, so the poisoned item blocked every run behind it forever."""
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.broadcast.create({"run_id": "evening", "pacing": {"min_gap_ms": None},
                              "items": rig.items((IVAN, "eins"))})
    assert (caught.value.code, caught.value.status_code) == ("invalid_request", 400)
    assert "min_gap_ms" in caught.value.message
    assert rig.ledger.get_run("evening") is None


def test_a_pacing_key_the_governor_does_not_read_is_refused_not_echoed_back(rig):
    """min_gap_sec is not min_gap_ms. Accepted and stored, it reads back off the run view as if it
    were honoured while the campaign goes out at the four-second floor."""
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.broadcast.create({"run_id": "evening", "pacing": {"min_gap_sec": 3600},
                              "items": rig.items((IVAN, "eins"))})
    assert "min_gap_sec" in caught.value.message and "min_gap_ms" in caught.value.message
    assert rig.ledger.get_run("evening") is None


def test_the_constraints_of_a_single_send_are_read_at_the_boundary_too(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.send_message(to=IVAN, body="eins", client_msg_id=KEYS[0], action="reply",
                             constraints={"daily_cap": None})
    assert (caught.value.code, caught.value.status_code) == ("invalid_request", 400)
    assert rig.driver.sent == [] and rig.ledger.get(KEYS[0]) is None


def test_a_bug_attempting_an_item_is_recorded_against_it_and_the_queue_moves_on(rig):
    """An unclassified exception used to leave the item queued, and the runner re-raised on the
    same item every poll: one bug and the whole rail was dead until a human guessed which run."""
    rig.broadcast.create({"run_id": "aaa-poisoned", "items": rig.items((IVAN, "eins"))})
    rig.clock.advance(60)
    rig.broadcast.create({"run_id": "zzz-behind", "items": [
        {"client_msg_id": KEYS[4], "to": PARTNER, "body": "zwei", "action": "reply"}]})
    send = rig.executor.send
    rig.executor.send = lambda req: (_ for _ in ()).throw(TypeError("not a real number")) \
        if req["body"] == "eins" else send(req)
    runner = B.BroadcastRunner(rig.broadcast, interval=0.0)

    assert runner.cycle() is None                         # counted and journalled, never swallowed
    item = rig.broadcast.view("aaa-poisoned")["items"][0]
    assert (item["status"], item["code"]) == (L.ITEM_FAILED, "executor_error")
    assert "not a real number" in item["detail"]
    assert runner.errors == 1
    assert "aaa-poisoned" in runner.last_error and KEYS[0] in runner.last_error, \
        "the poisoned run has to be nameable from /v1/health"

    runner.cycle()
    assert rig.driver.sent == ["zwei"], "the run behind the bug is reached on the next poll"


def test_a_stop_that_arrives_after_the_last_item_leaves_the_finished_run_alone(rig):
    rig.broadcast.create({"run_id": "probe-1", "items": rig.items((IVAN, "eins"))})
    rig.broadcast.step()
    finished = rig.broadcast.view("probe-1")["run"]
    rig.clock.advance(600)

    view = rig.broadcast.stop("probe-1")

    assert view["run"]["state"] == L.RUN_DONE and view["run"]["stop_requested"] is False
    assert view["run"]["finished_at"] == finished["finished_at"]
    assert view["counts"] == {"sent": 1}


def test_the_runner_cycle_never_raises_out(rig):
    runner = B.BroadcastRunner(rig.broadcast, interval=0.0)
    rig.broadcast.create({"run_id": "probe-1", "items": rig.items((IVAN, "eins"))})

    def explode():
        raise RuntimeError("a bug in our own code")
    rig.broadcast.step = explode

    assert runner.cycle() is None
    assert runner.errors == 1 and "a bug in our own code" in runner.last_error
    assert runner.heartbeat()["errors"] == 1


def journal(rig, event):
    return [dict(r) for r in rig.ledger._db.execute(
        "select * from journal where event=?", (event,)).fetchall()]


# --- the archive, which is the only thing between a cleanup and seven chats nobody authorised ---
ARCHIVED = "Alte Bewerberin"
WITH_ARCHIVE = {**CHATS, ARCHIVED: {"phone": "+491700000009", "unread": 0, "stamp": "18.08.26",
                                    "archived": True,
                                    "bubbles": bubbles(("in", "Guten Tag", "11:02"))}}


@pytest.fixture
def archive_rig(tmp_path):
    r = Rig(tmp_path, chats=WITH_ARCHIVE)
    yield r
    r.ledger.close()


def test_an_archived_chat_is_listed_as_archived(archive_rig):
    rows = {c["title"]: c for c in archive_rig.ops.list_chats()["chats"]}
    assert rows[ARCHIVED]["archived"] is True
    assert [t for t, c in rows.items() if c["archived"]] == [ARCHIVED]
    assert archive_rig.ops.list_chats(include_archived=False)["count"] == len(CHATS)


def test_a_finished_run_is_swept_with_its_bodies_and_an_open_one_is_not(rig):
    rig.broadcast.create({"run_id": "done-1", "items": rig.items((IVAN, "eins"))})
    rig.broadcast.step()
    rig.broadcast.create({"run_id": "open-1", "items": [
        {"client_msg_id": KEYS[4], "to": PARTNER, "body": "zwei", "action": "reply"}]})

    swept = rig.ledger.sweep(rig.clock.now + timedelta(days=L.LEDGER_RETENTION_DAYS + 1))

    assert (swept["broadcast_runs"], swept["broadcast_items"]) == (1, 1)
    assert rig.ledger.get_run("done-1") is None
    assert rig.ledger.get_run("open-1") is not None    # still owed to somebody


# --- the HTTP surface -------------------------------------------------------------------------
@pytest.fixture
def http(tmp_path):
    rig = Rig(tmp_path)
    server = S.BridgeServer(rig.executor, "s3cret", port=0, log=lambda *a: None,
                            operations=rig.ops, broadcast=rig.broadcast)
    server.dispatcher.start()  # TASK-227: every phone-touching route now enqueues onto this
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield rig, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    server.dispatcher.stop()
    rig.ledger.close()


def call(base, path, *, token="s3cret", payload=None):
    """-> (status, body). TASK-227: a phone-touching route answers 200 {"op_id","state":"queued"}
    first -- this polls GET /v1/ops/<id> until terminal and unwraps it back to the (status, body)
    a synchronous route used to answer directly, the same way app/wa/bridge.py::Client._await_op
    does for production callers."""
    status, body = _raw_call(base, path, token=token, payload=payload)
    if status == 200 and isinstance(body, dict) and body.get("state") == "queued" and body.get("op_id"):
        return _await_op(base, body["op_id"], token=token)
    return status, body


def _raw_call(base, path, *, token="s3cret", payload=None):
    req = urllib.request.Request(base + path, method="POST" if payload is not None else "GET",
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def _await_op(base, op_id, *, token, deadline_sec=10.0):
    deadline = time.monotonic() + deadline_sec
    while True:
        status, body = _raw_call(base, f"/v1/ops/{op_id}", token=token)
        if status == 200 and body.get("state") == "done":
            return 200, body["result"]
        if status == 200 and body.get("state") == "failed":
            error = body["error"]
            return error["error"]["http_status"], error
        if time.monotonic() >= deadline:
            raise AssertionError(f"op {op_id} did not finish within {deadline_sec}s: {body!r}")
        time.sleep(0.02)


def test_chats_goes_through_the_ops_queue_instead_of_calling_list_chats_inline(http):
    """TASK-269: GET /v1/chats used to call operations.list_chats() straight through inside
    do_GET -- the same TASK-227 leftover TASK-230 already fixed for /v1/reconcile -- so it could
    win huawei01.lock ahead of an already-queued op regardless of arrival order, and never
    produced an op_id for a caller to poll or for /v1/ops/<id> to report on. Fails before the fix
    (the raw response is the chat list itself, with no "state"/"op_id"); passes after it (the raw
    response is {"state": "queued", "op_id": ...}, unwrapped by call() exactly like every other
    phone-touching route)."""
    _rig, base = http
    status, raw = _raw_call(base, "/v1/chats")
    assert (status, raw.get("state"), "op_id" in raw) == (200, "queued", True)
    assert call(base, "/v1/chats")[1]["count"] == 3


def test_the_new_routes_answer_on_loopback(http):
    rig, base = http
    assert call(base, "/v1/chats")[1]["count"] == 3
    assert call(base, "/v1/thread?phone=" + IVAN.replace("+", "%2B"))[1]["count"] == 2
    status, body = call(base, "/v1/broadcasts", payload={
        "run_id": "probe-1", "items": [{"client_msg_id": KEYS[0], "to": IVAN, "body": "eins",
                                        "action": "reply"}]})
    assert (status, body["counts"]) == (200, {"queued": 1})
    assert call(base, "/v1/broadcasts")[1]["runs"][0]["run_id"] == "probe-1"
    assert call(base, "/v1/broadcasts/probe-1")[1]["items"][0]["status"] == "queued"
    assert call(base, "/v1/broadcasts/probe-1/stop", payload={})[1]["run"]["state"] == "stopped"
    assert call(base, "/v1/audit")[1]["rows"] == []


def test_health_carries_the_broadcast_and_audit_counters(http):
    rig, base = http
    call(base, "/v1/broadcasts", payload={
        "run_id": "probe-1", "items": [{"client_msg_id": KEYS[0], "to": IVAN, "body": "eins",
                                        "action": "reply"}]})
    health = call(base, "/v1/health")[1]
    assert health["broadcast"]["runs_open"] == 1
    assert health["broadcast"]["runner"] is None       # no runner thread in this rig
    assert health["audit"]["destructions"] == 0
