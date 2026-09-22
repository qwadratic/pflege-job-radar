"""Offline proof for the handset operations (TASK-147). FakeDriver only -- no adb, no phone.

What each test here is really about is a thing that would otherwise be found out on the handset:
a broadcast that re-sends a delivered recipient after a restart, a run that one bad number ends,
a delete that hits the row below the one that was approved, a clear that reports success over a
conversation still sitting on the phone.
"""
import copy
import json
import threading
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


# --- clear_chat / delete_chat -----------------------------------------------------------------
def test_a_delete_without_confirm_taps_nothing(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", phone=IVAN)
    assert (caught.value.code, caught.value.status_code) == ("invalid_request", 400)
    assert rig.driver.deleted == [] and rig.driver.lock_events == []
    assert rig.ledger.audit_rows() == []


def test_a_clear_without_confirm_taps_nothing(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.clear_chat(chat="Ivan Test", phone=IVAN, confirm=False)
    assert caught.value.status_code == 400
    assert rig.driver.cleared == []


def test_a_delete_refuses_when_the_number_is_not_the_one_on_the_row(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", phone=PARTNER, confirm=True)
    assert (caught.value.code, caught.value.status_code) == ("chat_identity_mismatch", 409)
    assert rig.driver.deleted == []
    assert rig.ledger.audit_rows() == []


def test_a_delete_refuses_when_two_rows_carry_the_name(rig):
    rig.driver.duplicate_titles = ["Ivan Test"]
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", confirm=True)
    assert caught.value.code == "chat_identity_mismatch"
    assert rig.driver.deleted == []


def test_a_delete_refuses_when_the_display_name_is_ambiguous(rig):
    rig.driver.ambiguous_titles = ["Ivan Test"]
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", confirm=True)
    assert caught.value.code == "chat_identity_mismatch"


def test_a_delete_refuses_a_chat_that_is_not_on_the_list(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Somebody Else", confirm=True)
    assert (caught.value.code, caught.value.status_code) == ("chat_not_found", 404)
    assert caught.value.message == ("no chat with that title is on the handset's list, and this "
                                    "executor's audit records no destruction of it either")
    assert "destroyed_by_us" not in caught.value.detail, "we did not remove what was never here"


def test_a_delete_of_a_chat_this_executor_already_deleted_says_so_and_when(rig):
    """2026-09-21: the first delete's answer was lost to a client timeout, the operator ran it
    again, and this refusal said only "no chat with that title is on the handset's list" -- which
    he read as the tool having matched the wrong chat. The audit is on this machine and it knows
    exactly why that row is gone."""
    rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)
    deleted_at = L.utc(rig.clock.now)
    rig.clock.advance(120)

    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert (caught.value.code, caught.value.status_code) == ("chat_not_found", 404)
    assert caught.value.message == (f"no chat with that title is on the handset's list: this "
                                    f"executor deleted it at {deleted_at} (audit 1)")
    record = caught.value.detail["destroyed_by_us"]
    assert (record["audit_id"], record["at"], record["verified"]) == (1, deleted_at, True)
    assert record["detail"]["destroyed"]["visible_messages"] == 2
    assert rig.driver.deleted == ["Ivan Test"], "the second call tapped nothing"
    assert len(rig.ledger.audit_rows()) == 1, "and recorded no second destruction"


def test_the_404_says_whether_that_row_is_about_the_number_the_caller_named(tmp_path):
    """A title is not an identity -- two contacts can wear one display name, which every other path
    here refuses on. The client cannot check it (neither number may travel), so the comparison is
    made on the machine that holds both and travels as one word.

    The third case is the live ledger's first row: a saved contact the handset could not resolve to
    a number at all, recorded with ``to_phone`` null. That row is neither this conversation nor
    somebody else's, and saying either would be the 2026-09-21 lie."""
    chats = {**copy.deepcopy(CHATS),
             "Valentyn NDT": {"phone": None, "unread": 0, "stamp": "21:49", "archived": False,
                              "bubbles": bubbles(("in", "Hallo", "21:26"))}}
    rig = Rig(tmp_path, chats=chats)
    rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)
    rig.ops.delete_chat(chat="Valentyn NDT", confirm=True)
    assert [r["to_phone"] for r in rig.ledger.audit_rows()] == [None, IVAN], \
        "the handset could not say whose 'Valentyn NDT' is, and the row says so rather than guessing"

    def verdict(title, phone):
        with pytest.raises(E.BridgeRefusal) as caught:
            rig.ops.delete_chat(chat=title, phone=phone, confirm=True)
        return caught.value.detail["destroyed_by_us"]["named_number"]

    assert verdict("Ivan Test", IVAN) == O.NUMBER_SAME
    assert verdict("Ivan Test", PARTNER) == O.NUMBER_DIFFERENT, \
        "same title, another person: this row is no answer about that conversation"
    assert verdict("Valentyn NDT", PARTNER) == O.NUMBER_UNRECORDED, \
        "the record never held a number, so it can neither match nor rule one out"
    assert verdict("Ivan Test", None) is None, "nothing was named for the row to be compared to"
    assert rig.driver.deleted == ["Ivan Test", "Valentyn NDT"], "no second call tapped anything"
    rig.ledger.close()


def test_a_clear_does_not_explain_a_missing_row(rig):
    """A cleared chat stays on the list -- that is what clear_chat promises -- so a clear in the
    record is no reason for a row to be absent, and claiming it would be the same guess pointed the
    other way."""
    rig.ops.clear_chat(chat="Ivan Test", phone=IVAN, confirm=True)
    del rig.driver.chats["Ivan Test"]                 # somebody deleted it on the handset itself

    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", confirm=True)

    assert "records no destruction of it either" in caught.value.message
    assert "destroyed_by_us" not in caught.value.detail


def test_a_delete_refuses_when_the_conversation_moved_on_since_the_caller_looked(rig):
    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True, expect_messages=9)
    assert caught.value.code == "chat_identity_mismatch"
    assert caught.value.message == ("delete_chat was approved for 9 visible messages and the "
                                    "handset now shows 2"), "nothing we did explains a bigger count"
    assert "emptied_by_us" not in caught.value.detail
    assert caught.value.detail["on_handset"] == 2
    assert rig.driver.deleted == []


def test_a_count_that_shrank_because_we_emptied_it_says_so_instead_of_leaving_it_open(rig):
    """The clear-chat twin of the 2026-09-21 incident: the clear went through, its answer was lost
    with the terminal, and the operator retyped the command with the count they had approved. "I
    approved 3 and it shows 0" invites exactly the inference that cost the hour -- that the tool
    matched the wrong chat. The reason is a row in this executor's own ledger."""
    rig.ops.clear_chat(chat="Ivan Test", phone=IVAN, confirm=True, expect_messages=2)
    cleared_at = L.utc(rig.clock.now)
    rig.clock.advance(120)

    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.clear_chat(chat="Ivan Test", phone=IVAN, confirm=True, expect_messages=2)

    assert caught.value.code == "chat_identity_mismatch"
    assert caught.value.message == (f"clear_chat was approved for 2 visible messages and the "
                                    f"handset now shows 0: this executor cleared that conversation "
                                    f"at {cleared_at} (audit 1, verified=True), which is why there "
                                    f"is less there than when it was read")
    assert caught.value.detail["emptied_by_us"] == {"audit_id": 1, "at": cleared_at,
                                                    "verified": True}
    assert rig.driver.cleared == ["Ivan Test"], "the second call tapped nothing"
    assert caught.value.detail["on_handset"] == 0


def test_a_delete_reports_what_it_destroyed_and_proves_the_chat_is_gone(rig):
    result = rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True, expect_messages=2)

    assert result["destroyed"]["visible_messages"] == 2
    assert (result["destroyed"]["incoming"], result["destroyed"]["outgoing"]) == (1, 1)
    assert result["destroyed"]["unread_before"] == 1
    assert result["verification"] == {"verified": True, "method": "chat_list_rescan",
                                      "chat_present": False, "chats_on_list": 2, "why": ""}
    assert "Ivan Test" not in rig.driver.chats
    assert rig.driver.lock_events == ["acquire", "release"]   # preview and act, one acquisition


def test_a_clear_empties_the_conversation_and_keeps_it(rig):
    result = rig.ops.clear_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert result["destroyed"]["visible_messages"] == 2
    assert result["verification"]["verified"] is True
    assert result["verification"]["messages_left"] == 0
    assert "Ivan Test" in rig.driver.chats              # the chat itself is still there
    assert rig.driver.chats["Ivan Test"]["bubbles"] == []
    assert result["ui"]["starred_included"] is True


def test_a_clear_that_left_messages_behind_is_an_error_not_a_shrug(rig):
    rig.driver.clear_leaves = 1                        # a starred message the sheet did not cover

    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.clear_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert (caught.value.code, caught.value.status_code) == ("destruction_unverified", 504)
    assert "1 messages are still on the thread" in caught.value.message
    row = rig.ledger.audit_rows()[0]
    assert (row["operation"], row["verified"]) == ("clear_chat", False)


def test_a_delete_the_handset_did_not_perform_is_an_error(rig):
    rig.driver.delete_leaves_row = True                # the dialog was confirmed, the row remains

    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert caught.value.code == "destruction_unverified"
    assert caught.value.detail["verification"]["chat_present"] is True
    assert rig.ledger.audit_rows()[0]["verified"] is False


def test_every_destruction_writes_an_audit_row(rig):
    rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)
    rig.clock.advance(60)
    rig.ops.clear_chat(chat="Soak Rail", phone=SOAK, confirm=True)

    rows = rig.ledger.audit_rows()
    assert [r["operation"] for r in rows] == ["clear_chat", "delete_chat"]   # newest first
    deleted = rows[1]
    assert deleted["chat_title"] == "Ivan Test" and deleted["to_phone"] == IVAN
    assert deleted["verified"] is True
    assert deleted["detail"]["destroyed"]["visible_messages"] == 2
    assert deleted["detail"]["proof"]["method"] == "chat_list_rescan"
    assert deleted["at"] == L.utc(rig.clock.now - timedelta(seconds=60))


def test_a_destruction_whose_verification_cannot_run_is_recorded_and_is_a_504(rig):
    """The taps landed and the handset will not answer for them. That is the state this rail calls
    destruction_unverified -- and the record of what was destroyed cannot depend on the rescan
    that just failed, which is why the audit row is written before the first tap."""
    tap = rig.driver.delete_chat_row

    def delete_then_drop_off_adb(title, *, archived=False):
        out = tap(title, archived=archived)
        rig.driver.fail_on_list = "the chat list would not come to the front (focus=Conversation)"
        return out
    rig.driver.delete_chat_row = delete_then_drop_off_adb

    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert (caught.value.code, caught.value.status_code) == ("destruction_unverified", 504)
    assert rig.driver.deleted == ["Ivan Test"]            # the conversation is gone
    row = rig.ledger.audit_rows()[0]
    assert (row["chat_title"], row["to_phone"], row["verified"]) == ("Ivan Test", IVAN, False)
    assert row["detail"]["destroyed"]["visible_messages"] == 2
    assert "would not come to the front" in row["detail"]["proof"]["why"]
    assert caught.value.detail["audit_id"] == row["id"]


def test_a_verb_that_raises_mid_menu_is_unverified_rather_than_a_bug_report(rig):
    """The menu walk itself failing is not knowledge that nothing happened: the taps before it
    landed. 504 with a row, not 500 with none."""
    rig.driver.clear_chat_history = lambda *a, **kw: (_ for _ in ()).throw(
        D.DriverError("the clear-chat sheet lost its confirm button"))

    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.clear_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert caught.value.code == "destruction_unverified"
    assert rig.ledger.audit_rows()[0]["detail"]["state"] == "unproved"


def test_a_clear_that_removed_the_chat_is_not_reported_as_a_clear(rig):
    """clear_chat keeps the conversation; that is the whole difference from delete. A number can
    only prove a thread is empty -- opening one WhatsApp has no thread for draws an empty one --
    so the row still being on the list is the half of the verdict that says which verb ran."""
    def clear_that_deletes(title, *, archived=False, include_starred=True):
        rig.driver.chats[title]["bubbles"] = []
        rig.driver.vanished_titles.append(title)          # the menu item landed on delete
        rig.driver.cleared.append(title)
        return {"menu_item": "Chat leeren", "confirmed_with": "primary_button",
                "starred_included": True}
    rig.driver.clear_chat_history = clear_that_deletes

    with pytest.raises(E.BridgeRefusal) as caught:
        rig.ops.clear_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert caught.value.code == "destruction_unverified"
    assert "removed, not emptied" in caught.value.message
    row = rig.ledger.audit_rows()[0]
    assert (row["operation"], row["verified"]) == ("clear_chat", False)


def test_a_park_failure_does_not_rewrite_a_destruction_that_was_proved(rig):
    """The same rule executor.send applies to a delivered message: tidying up is journalled, never
    promoted into the caller's answer."""
    rig.driver.park = lambda: (_ for _ in ()).throw(D.DriverError("adb focus did not return"))

    result = rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert result["verification"]["verified"] is True
    assert rig.ledger.audit_rows()[0]["verified"] is True
    assert journal(rig, "park_failed"), "the failure is recorded where failures live"


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


def test_a_cleanup_cannot_reach_an_archived_chat_without_saying_archived(archive_rig):
    """The flag is an assertion about which list the chat is on. Without it the archive is not a
    place a destructive call can land by accident."""
    with pytest.raises(E.BridgeRefusal) as caught:
        archive_rig.ops.delete_chat(chat=ARCHIVED, confirm=True)

    assert (caught.value.code, caught.value.status_code) == ("chat_not_found", 404)
    assert caught.value.message == ("no chat with that title is on the main chat list: the handset "
                                    "draws that conversation in the archive")
    assert caught.value.detail["in_other_folder"] is True, "it exists; it is one folder over"
    assert archive_rig.driver.deleted == []
    assert archive_rig.ledger.audit_rows() == []


def test_an_archived_chat_is_destroyed_when_it_is_named_as_archived(archive_rig):
    result = archive_rig.ops.delete_chat(chat=ARCHIVED, archived=True, confirm=True)

    assert result["verification"]["verified"] is True
    assert archive_rig.driver.deleted == [ARCHIVED]
    assert archive_rig.ledger.audit_rows()[0]["chat_title"] == ARCHIVED


def test_a_main_list_chat_named_as_archived_is_not_found(archive_rig):
    """The mirror case: the assertion is checked, not dropped, in both directions."""
    with pytest.raises(E.BridgeRefusal) as caught:
        archive_rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, archived=True, confirm=True)
    assert caught.value.code == "chat_not_found"
    assert caught.value.message == ("no chat with that title is on the archive: the handset draws "
                                    "that conversation in the main chat list")
    assert archive_rig.driver.deleted == []


def test_a_chat_in_the_other_folder_is_never_explained_by_the_audit(archive_rig):
    """A title that was deleted once and belongs to a live conversation again -- same display name,
    new chat, now archived -- must not be answered with the old record. The conversation is on the
    handset; which folder it is in is the whole refusal."""
    archive_rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)
    archive_rig.driver.chats["Ivan Test"] = {"phone": IVAN, "unread": 0, "stamp": "10:00",
                                             "archived": True, "bubbles": []}

    with pytest.raises(E.BridgeRefusal) as caught:
        archive_rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)

    assert caught.value.detail["in_other_folder"] is True
    assert "destroyed_by_us" not in caught.value.detail, "it is not gone; it is archived"


def test_the_audit_outlives_the_retention_sweep(rig):
    """The record of a destruction has to outlive the thing it recorded."""
    rig.ops.delete_chat(chat="Ivan Test", phone=IVAN, confirm=True)
    rig.ledger.sweep(rig.clock.now + timedelta(days=L.LEDGER_RETENTION_DAYS + 1))
    assert len(rig.ledger.audit_rows()) == 1


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


def test_the_destructive_routes_keep_the_confirm_rule_over_the_wire(http):
    rig, base = http
    status, body = call(base, "/v1/chats/delete", payload={"chat": "Ivan Test", "phone": IVAN})
    assert (status, body["error"]["code"]) == (400, "invalid_request")
    assert rig.driver.deleted == []

    status, body = call(base, "/v1/chats/delete",
                        payload={"chat": "Ivan Test", "phone": IVAN, "confirm": True})
    assert (status, body["verification"]["verified"]) == (200, True)
    assert call(base, "/v1/audit")[1]["rows"][0]["operation"] == "delete_chat"


def test_a_misspelled_confirm_flag_is_refused_rather_than_ignored(http):
    rig, base = http
    status, body = call(base, "/v1/chats/delete",
                        payload={"chat": "Ivan Test", "confirm_delete": True})
    assert (status, body["error"]["code"]) == (400, "invalid_request")
    assert "confirm_delete" in body["error"]["message"]
    assert rig.driver.deleted == []


def test_an_unverified_destruction_is_a_504_on_the_wire(http):
    rig, base = http
    rig.driver.delete_leaves_row = True
    status, body = call(base, "/v1/chats/delete",
                        payload={"chat": "Ivan Test", "phone": IVAN, "confirm": True})
    assert (status, body["error"]["code"]) == (504, "destruction_unverified")
    assert body["error"]["retryable"] is False


def test_health_carries_the_broadcast_and_audit_counters(http):
    rig, base = http
    call(base, "/v1/broadcasts", payload={
        "run_id": "probe-1", "items": [{"client_msg_id": KEYS[0], "to": IVAN, "body": "eins",
                                        "action": "reply"}]})
    health = call(base, "/v1/health")[1]
    assert health["broadcast"]["runs_open"] == 1
    assert health["broadcast"]["runner"] is None       # no runner thread in this rig
    assert health["audit"]["destructions"] == 0
