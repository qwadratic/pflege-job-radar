"""Offline tests for the handset operator CLI and its client half (TASK-147).

No phone, no network: every client here is built with an injected transport, the seam
``app/wa/bridge.py`` already has. The tests are written against what an OPERATOR observes -- the exit
code, what reached the executor, what did not -- because the point of these tools is that one
command does exactly one thing and lies about none of it. Synthetic numbers only.
"""
import importlib.util
import json
import pathlib
from datetime import datetime, timezone

import pytest

from app.wa import bridge as BR
from app.wa import bridge_ids as BI
from app.wa import config as C
from app.wa import store as ST

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("wa_bridge_cli", ROOT / "tools" / "wa_bridge.py")
CLI = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CLI)

BASE = "http://127.0.0.1:8793"
MINE = "+4915550000001"
PARTNER = "+4915550000002"
# The chat list as bridge/operations.py::list_chats reports it: the handset's own rows plus what it
# can prove about whose row each one is.
CHATS = (200, {"ok": True, "count": 2, "chats": [
    {"title": "Ivan Test", "phone": MINE, "phone_source": "address_book", "ambiguous": False,
     "unread": 0, "stamp": "10:04", "archived": False, "has_preview": True},
    {"title": "Partner Test", "phone": PARTNER, "phone_source": "address_book", "ambiguous": False,
     "unread": 2, "stamp": "GESTERN", "archived": False, "has_preview": True}]})
THREAD = (200, {"ok": True, "chat": {"title": "Ivan Test", "phone": MINE}, "count": 2,
                "visibility": "visible_without_scrolling", "incoming": 1, "outgoing": 1,
                "messages": [{"direction": "out", "clock": "10:00", "tick": "Gelesen", "body": "Hallo",
                              "body_len": 5},
                             {"direction": "in", "clock": "10:01", "tick": None, "body": "Hi",
                              "body_len": 2}]})


class FakeBridge:
    """The executor, offline. Answers per route (a tuple, a callable taking the posted body, or an
    exception to raise) and records every call. An unanswered route fails the test instead of
    reaching the network -- that is how "this command posted nothing" is asserted."""

    def __init__(self, **answers):
        self.answers = {"/" + name.replace("_", "/"): a for name, a in answers.items()}
        self.calls = []

    def __call__(self, method, url, headers=None, data=None, timeout=None):
        path, _, query = url[len(BASE):].partition("?")
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "path": path, "query": query, "body": body, "timeout": timeout})
        answer = self.answers.get(path)
        if answer is None:
            raise AssertionError(f"unexpected call to the bridge: {method} {path}")
        if isinstance(answer, Exception):
            raise answer
        return answer(body) if callable(answer) else answer

    def posted(self, path):
        return [c for c in self.calls if c["path"] == path and c["method"] == "POST"]


def run(argv, now=None, **answers):
    """-> (exit code, the fake executor).

    ``now`` pins the client's clock. The audit-aware paths compare an audit row's moment against
    the moment the call was made, so a test that let the real clock through would pass today and
    fail tomorrow -- and it is the comparison itself that is under test.
    """
    fake = FakeBridge(**answers)
    client = BR.Client(transport=fake, base_url=BASE, token="tok",
                       **({"now": lambda: now} if now is not None else {}))
    return CLI.main(argv, client=client), fake


def sent_echo(body):
    """The executor's terminal answer to one bubble: the key we posted, with a tick."""
    return 200, {"ok": True, "state": "sent", "client_msg_id": body["client_msg_id"],
                 "verified": {"tick": "Zugestellt"}, "sent_at": "2026-09-21T10:00:00Z"}


def run_view(statuses, run_id="b1", state="open", drop=0):
    """A run view as bridge/broadcast.py answers it: one item per recipient, no bodies and no
    numbers -- a thread tag and the key. ``drop`` leaves that many posted items out of the view."""
    def answer(body):
        items = [{"client_msg_id": item["client_msg_id"], "position": i, "thread": f"…{i}",
                  "status": status, "code": None, "detail": "Zugestellt" if status == "sent" else None,
                  "attempts": 1, "next_attempt_at": None}
                 for i, (item, status) in enumerate(zip(body.get("items") or [], statuses))]
        kept = items[:len(items) - drop] if drop else items
        return 200, {"ok": True, "run": {"run_id": run_id, "state": state},
                     "counts": {s: statuses.count(s) for s in set(statuses)}, "items": kept}
    return answer


def audit_row(**over):
    """One row of the destruction record, as bridge/ledger.py leaves it after ``finish_audit``."""
    row = {"id": 3, "at": "2026-09-21T17:26:16.912Z", "operation": "delete_chat",
           "chat_title": "Ivan Test", "chat_tag": "3a8400e42ee6", "to_phone": MINE,
           "verified": True,
           "detail": {"state": "verified",
                      "destroyed": {"visible_messages": 3, "incoming": 1, "outgoing": 2,
                                    "visibility": "visible_without_scrolling"},
                      "ui": {"menu_item": "Chat löschen", "confirmed_with": "Chat löschen"},
                      "proof": {"verified": True, "method": "chat_list_rescan",
                                "chat_present": False, "chats_on_list": 7, "why": ""}}}
    row.update(over)
    return row


def audit(*rows):
    return 200, {"ok": True, "rows": list(rows)}


# What the transport raises when the executor is still working and we stopped listening. The text
# is the one app/wa/bridge.py::_default_transport writes, because the operator reads it.
TIMED_OUT = BR.BridgeError("bridge did not answer within 222.0s: the executor may still be working",
                           status_code=BR.UNCERTAIN_STATUS, code=BR.CODE_ANSWER_TIMEOUT)


def recipients(tmp_path, text, suffix=".csv"):
    path = tmp_path / ("recipients" + suffix)
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture
def autosend(monkeypatch):
    """The harness-wide send switch the Meta rail already obeys; off by default in tests."""
    monkeypatch.setattr(C, "AUTOSEND", True)


# --- a broadcast plans before it sends --------------------------------------------------------------

def test_a_broadcast_is_planned_and_not_queued_without_send(tmp_path, capsys):
    file = recipients(tmp_path, f"phone\n{MINE}\n{PARTNER}\n")
    code, fake = run(["broadcast", "--id", "b1", "--file", file, "--body", "Hallo"])
    out = capsys.readouterr().out
    assert code == 0 and fake.calls == [], "a dry run reaches the executor not at all"
    assert "2 recipient(s)" in out and "dry run" in out
    assert BI.campaign_key(campaign_id="b1", phone=MINE, attempt=1) in out, "the keys are printed to be checked"


def test_a_broadcast_send_still_needs_the_harness_switch(tmp_path, capsys):
    file = recipients(tmp_path, f"phone\n{MINE}\n")
    code, fake = run(["broadcast", "--id", "b1", "--file", file, "--body", "Hallo", "--send"])
    assert code == 2 and fake.calls == []
    assert "WA_AUTOSEND=1" in capsys.readouterr().err


def test_a_broadcast_is_one_call_with_one_deterministic_key_per_recipient(tmp_path, autosend):
    file = recipients(tmp_path, f"phone,body\n{MINE},Hallo\n{PARTNER},Servus\n")
    code, fake = run(["broadcast", "--id", "b1", "--file", file, "--send", "--pacing", '{"min_gap_sec": 300}'],
                     v1_broadcasts=run_view(["sent", "sent"]))
    posted = fake.posted(BR.BROADCASTS_PATH)
    assert code == 0 and len(posted) == 1, "many recipients, one call: the executor's runner owns the pacing"
    assert [i["client_msg_id"] for i in posted[0]["body"]["items"]] == [
        BI.campaign_key(campaign_id="b1", phone=MINE, attempt=1),
        BI.campaign_key(campaign_id="b1", phone=PARTNER, attempt=1)], "a re-run replays instead of resending"
    assert [i["body"] for i in posted[0]["body"]["items"]] == ["Hallo", "Servus"], "a per-row body wins"
    assert {i["action"] for i in posted[0]["body"]["items"]} == {BR.PACING_FIRST_TOUCH}, \
        "the governor paces every item as cold contact"
    assert posted[0]["body"]["pacing"] == {"min_gap_sec": 300}, "pacing is passed through, never interpreted here"


def test_a_recipient_file_with_a_bad_number_sends_to_nobody(tmp_path, capsys, autosend):
    file = recipients(tmp_path, f"phone\n{MINE}\nnot a number\n{PARTNER}\n")
    code, fake = run(["broadcast", "--id", "b1", "--file", file, "--body", "Hallo", "--send"])
    assert code == 2 and fake.calls == [], "the whole file is refused; a silently skipped recipient is unauditable"
    assert "line 3" in capsys.readouterr().err, "the refusal names the line"


def test_a_repeated_recipient_is_refused_rather_than_messaged_twice(tmp_path, capsys, autosend):
    file = recipients(tmp_path, f"phone\n{MINE}\n{MINE.replace('+49', '0049')}\n")
    code, fake = run(["broadcast", "--id", "b1", "--file", file, "--body", "Hallo", "--send"])
    assert code == 2 and fake.calls == []
    assert "line 2" in capsys.readouterr().err, "the same human in two spellings is still the same human"


def test_a_recipient_with_no_body_at_all_is_refused(tmp_path, capsys, autosend):
    file = recipients(tmp_path, json.dumps([{"phone": MINE, "body": "Hallo"}, {"phone": PARTNER}]), ".json")
    code, fake = run(["broadcast", "--id", "b1", "--file", file, "--send"])
    assert code == 2 and fake.calls == []
    assert "no body" in capsys.readouterr().err


def test_a_refused_recipient_does_not_hide_behind_the_others(tmp_path, capsys, autosend):
    file = recipients(tmp_path, f"phone\n{MINE}\n{PARTNER}\n")
    code, _ = run(["broadcast", "--id", "b1", "--file", file, "--body", "Hallo", "--send"],
                  v1_broadcasts=run_view(["sent", "refused"]))
    assert code == 1, "one refusal does not abort the run, and it does not average away either"
    assert "refused" in capsys.readouterr().out


def test_a_key_the_run_view_never_mentions_is_not_a_queued_message(tmp_path, capsys, autosend):
    file = recipients(tmp_path, f"phone\n{MINE}\n{PARTNER}\n")
    code, _ = run(["broadcast", "--id", "b1", "--file", file, "--body", "Hallo", "--send"],
                  v1_broadcasts=run_view(["sent", "sent"], drop=1))
    assert code == 1, "we posted two keys and the run knows one: the second is unknown, not sent and not failed"
    assert BR.BROADCAST_NO_ANSWER in capsys.readouterr().out


def test_a_queued_run_is_unfinished_rather_than_wrong(tmp_path, capsys, autosend):
    file = recipients(tmp_path, f"phone\n{MINE}\n")
    code, _ = run(["broadcast", "--id", "b1", "--file", file, "--body", "Hallo", "--send"],
                  v1_broadcasts=run_view(["queued"]))
    assert code == 3, "queueing a run is not sending it: the runner does that, paced"
    assert "--status" in capsys.readouterr().out, "and the operator is told how to follow it"


def test_the_runs_on_the_executor_are_listable_without_naming_one(capsys):
    runs = (200, {"ok": True, "runs": [{"run_id": "b1", "state": "done", "counts": {"sent": 2}}]})
    code, fake = run(["broadcast", "--runs"], v1_broadcasts=runs)
    assert code == 0 and fake.calls[0]["method"] == "GET"
    assert "b1" in capsys.readouterr().out


def test_a_run_the_executor_does_not_know_is_a_refusal_not_an_empty_report(capsys):
    unknown = BR.BridgeError("bridge HTTP 400", status_code=400, code="invalid_request")
    code, _ = run(["broadcast", "--id", "nope", "--status"], **{"v1_broadcasts/nope": unknown})
    err = capsys.readouterr().err
    assert code == 1 and "400" in err and "invalid_request" in err


def test_a_running_broadcast_is_read_back_and_stopped_without_sending_anything(capsys):
    view = (200, {"ok": True, "run": {"run_id": "b1", "state": "stopped"}, "counts": {"sent": 1},
                  "items": [{"client_msg_id": "k1", "position": 0, "thread": "…01", "status": "sent",
                             "detail": "Gelesen"}]})
    code, fake = run(["broadcast", "--id", "b1", "--status"], **{"v1_broadcasts/b1": view})
    assert code == 0 and fake.calls[0]["method"] == "GET", "--status never sends"
    stopped, fake = run(["broadcast", "--id", "b1", "--stop"], **{"v1_broadcasts/b1/stop": view})
    assert stopped == 0 and fake.calls[0]["path"] == "/v1/broadcasts/b1/stop"
    assert "stopped" in capsys.readouterr().out


GONE = "+43 670 4048778"
DELETED_GONE = audit_row(chat_title=GONE, to_phone="+436704048778")


def test_a_media_sends_answer_timeout_is_never_printed_as_a_refusal(capsys, autosend):
    """TASK-247: send_photos/send_gallery/send_document queue for the handset before this client
    ever gives up listening (OPS_PATH's own contract, bridge.py:100-104), so a timed-out answer is
    never "bridge refused" -- and none of the three mint an idempotency key, so the operator must be
    told not to resend on a guess, the same 2026-09-21 lesson the destructive routes already carry."""
    code, fake = run(["send-gallery", "--to", MINE, "--files", "/tmp/a.jpg"], v1_gallery=TIMED_OUT)
    err = capsys.readouterr().err
    assert code == 1 and "refused" not in err
    assert "not a refusal" in err and "do not resend" in err
    assert f"tools/wa_bridge.py read --phone {MINE}" in err


def test_the_destruction_record_is_readable_on_its_own(capsys):
    """The command every one of those refusals tells an operator to run."""
    code, fake = run(["audit", "--title", GONE], v1_audit=audit(DELETED_GONE, audit_row()))
    out = capsys.readouterr().out
    assert code == 0 and [c["method"] for c in fake.calls] == ["GET"], "reading it touches no phone"
    assert "1 destruction(s) recorded" in out, "--title is the chat asked about, not every row"
    assert ("  3  2026-09-21T17:26:16.912Z  delete_chat  '+43 670 4048778'  verified=True  "
            "state='verified'  3 message(s)  chat_list_rescan") in out


# --- the timeout each route gets is the one that route costs ------------------------------------------

def test_a_send_budget_covers_the_flock_wait_the_op_queue_makes_it_pay(capsys, autosend):
    """TASK-146 measured the send itself; TASK-243 added the 30 s the op may spend waiting for the
    handset lock before any of that starts. Every sibling budget in app/wa/bridge.py already
    carried FLOCK_WAIT_SEC -- send was the one that did not, so a send that merely queued behind
    another op timed out as 'nothing was sent' while the executor went on to type it."""
    code, fake = run(["send", "--to", MINE, "--body", "x" * 320], v1_messages=sent_echo)
    expected = BR.FLOCK_WAIT_SEC + BR.EXECUTOR_FIXED_BUDGET_SEC + 320 / 3.2
    assert code == 0 and fake.posted(BR.MESSAGES_PATH)[0]["timeout"] == expected


# --- sending one message ------------------------------------------------------------------------------

def test_one_send_is_one_command_and_one_call(capsys, autosend):
    code, fake = run(["send", "--to", MINE, "--body", "Hallo"], v1_messages=sent_echo)
    out = capsys.readouterr().out
    assert code == 0 and len(fake.posted(BR.MESSAGES_PATH)) == 1
    assert fake.posted(BR.MESSAGES_PATH)[0]["body"]["to"] == MINE
    assert "Zugestellt" in out


def test_the_same_send_twice_carries_the_same_key_and_a_raised_attempt_does_not(autosend):
    _, first = run(["send", "--to", MINE, "--body", "Hallo"], v1_messages=sent_echo)
    _, again = run(["send", "--to", MINE, "--body", "Hallo"], v1_messages=sent_echo)
    _, third = run(["send", "--to", MINE, "--body", "Hallo", "--attempt", "2"], v1_messages=sent_echo)
    key = first.posted(BR.MESSAGES_PATH)[0]["body"]["client_msg_id"]
    assert again.posted(BR.MESSAGES_PATH)[0]["body"]["client_msg_id"] == key, "a re-run is a ledger replay"
    assert third.posted(BR.MESSAGES_PATH)[0]["body"]["client_msg_id"] != key, "--attempt is how you mean it twice"


def test_a_send_dry_run_and_a_send_without_the_switch_post_nothing(capsys):
    dry, fake = run(["send", "--to", MINE, "--body", "Hallo", "--dry-run"])
    assert dry == 0 and fake.calls == []
    off, fake = run(["send", "--to", MINE, "--body", "Hallo"])
    assert off == 2 and fake.calls == [] and "WA_AUTOSEND=1" in capsys.readouterr().err


def test_a_send_the_executor_only_accepted_is_not_finished(capsys, autosend):
    accepted = (202, {"ok": True, "state": "queued", "client_msg_id": "k"})
    code, _ = run(["send", "--to", MINE, "--body", "Hallo"], v1_messages=accepted)
    assert code == 3 and "ACCEPTED" in capsys.readouterr().err, "queued is not sent, and never printed as sent"


def test_a_local_spelling_reaches_the_rail_as_e164(capsys, autosend):
    code, fake = run(["send", "--to", "015550000001", "--body", "Hallo"], v1_messages=sent_echo)
    assert code == 0 and fake.posted(BR.MESSAGES_PATH)[0]["body"]["to"] == MINE


# --- reading, listing, and what the bridge's refusals look like ----------------------------------------

def test_the_chat_list_and_one_thread_are_read_only(capsys):
    assert run(["chats"], v1_chats=CHATS)[0] == 0
    assert "Partner Test" in capsys.readouterr().out
    code, fake = run(["read", "--phone", MINE], v1_thread=THREAD)
    assert code == 0 and [c["method"] for c in fake.calls] == ["GET"]
    assert "2 message(s)" in capsys.readouterr().out


def test_an_archived_chat_is_asked_for_as_text_not_as_a_python_bool(capsys):
    code, fake = run(["read", "--title", "Ivan Test", "--archived"], v1_thread=THREAD)
    assert code == 0 and fake.calls[0]["query"] == "chat=Ivan+Test&archived=1&include_text=1", \
        "'False' is a non-empty string in every naive query parser"


def test_the_bridges_own_refusal_code_reaches_the_operator(capsys):
    parked = BR.BridgeError("bridge HTTP 429", status_code=429, code="rail_parked",
                            payload={"error": {"code": "rail_parked"}})
    code, _ = run(["chats"], v1_chats=parked)
    err = capsys.readouterr().err
    assert code == 1 and "429" in err and "rail_parked" in err, "the executor's taxonomy is not flattened"


def test_an_unconfigured_rail_stops_before_any_command(capsys):
    assert CLI.main(["chats"], client=BR.Client(transport=FakeBridge(), base_url="", token="")) == 2
    assert "WA_BRIDGE_URL" in capsys.readouterr().err


def test_health_prints_what_the_rail_says_about_itself(capsys):
    health = (200, {"ok": True, "version": "1.2.3", "at": "2026-09-21T10:00:00Z",
                    "rail": {"number": None, "driver": {"kind": "adb"}}, "queue": {"pending": 0},
                    "quota": {"sent_today": 0}})
    assert run(["health"], v1_health=health)[0] == 0
    out = capsys.readouterr().out
    assert "1.2.3" in out and "UNVERIFIED" in out, "an unverified rail number is said out loud"


def test_health_says_a_weak_attribution_and_a_duplicate_out_loud(capsys):
    """TASK-131 round 6: a weak pick is visible in health, not just the row -- Ivan's own
    requirement that a human can audit one."""
    health = (200, {"ok": True, "version": "1.2.3", "at": "2026-09-21T10:00:00Z",
                    "rail": {"number": None, "driver": {"kind": "adb"}}, "queue": {"pending": 0},
                    "quota": {"sent_today": 0},
                    "media_watcher": {"unresolved_backlog": {"unresolved": 1, "duplicate_content": 1,
                                                             "weak_links": 2, "by_kind": {"image": 1}}},
                    "identity_watcher": {"attached_total": 5, "weak_total": 2, "errors": 0}})
    assert run(["health"], v1_health=health)[0] == 0
    out = capsys.readouterr().out
    assert "share bytes with another pull" in out
    assert "2 attribution(s) marked WEAK" in out
    assert "5 attached (2 weak), 0 errors" in out


def test_health_says_unresolved_sends_out_loud_when_the_queue_has_them(capsys):
    """TASK-261: the bare count was already in `queue` -- the age is the new information, and it
    has to point at `unresolved-list` the same way the media line points at `media-list`."""
    health = (200, {"ok": True, "version": "1.2.3", "at": "2026-09-21T10:00:00Z",
                    "rail": {"number": None, "driver": {"kind": "adb"}},
                    "queue": {"sent": 41, "unconfirmed": 2}, "quota": {"sent_today": 0},
                    "oldest_unresolved_sec": 518400})
    assert run(["health"], v1_health=health)[0] == 0
    out = capsys.readouterr().out
    assert "unresolved sends: 2 (oldest 518400s) -- see `unresolved-list`" in out


def test_health_says_nothing_about_unresolved_sends_when_the_queue_has_none(capsys):
    health = (200, {"ok": True, "version": "1.2.3", "at": "2026-09-21T10:00:00Z",
                    "rail": {"number": None, "driver": {"kind": "adb"}},
                    "queue": {"sent": 41}, "quota": {"sent_today": 0},
                    "oldest_unresolved_sec": None})
    assert run(["health"], v1_health=health)[0] == 0
    assert "unresolved sends" not in capsys.readouterr().out


# --- TASK-264: the liveness fields, printed every time, and the exit code they earn ------------------

HEALTH_NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def test_health_prints_the_liveness_fields_when_everything_is_alive(capsys):
    """Before this fix none of watcher/ops_dispatcher/phone_ops/inbound/retention printed anywhere
    but --json -- a live rail and a dead one looked identical on screen."""
    health = (200, {"ok": True, "version": "1.2.3", "at": "2026-09-23T12:00:00Z",
                    "rail": {"number": None, "driver": {"kind": "adb"}},
                    "queue": {"sent": 41}, "quota": {"sent_today": 0},
                    "watcher": {"alive": True, "last_ok_at": "2026-09-23T11:59:55Z"},
                    "ops_dispatcher": {"alive": True, "last_ok_at": "2026-09-23T11:59:58Z"},
                    "phone_ops": {"queued": 0, "oldest_queued_at": None},
                    "inbound": {"unacked": 0, "oldest_unacked_at": None},
                    "retention": {"last_ok_at": "2026-09-23T09:00:00Z", "errors": 0,
                                  "last_error": None, "last_error_at": None}})
    assert run(["health"], now=HEALTH_NOW, v1_health=health)[0] == 0
    out = capsys.readouterr().out
    assert "watcher: alive=True  last_ok_at='2026-09-23T11:59:55Z'  age=5s" in out
    assert "ops_dispatcher: alive=True  last_ok_at='2026-09-23T11:59:58Z'" in out
    assert "phone_ops: queued=0  oldest_queued_at=None" in out
    assert "inbound: unacked=0  oldest_unacked_at=None" in out
    assert "retention: last_ok_at='2026-09-23T09:00:00Z'  errors=0" in out
    assert "ATTENTION" not in out


def test_health_flags_a_dead_watcher_a_stuck_dispatcher_and_a_retention_error(capsys):
    """The scenario the finding names: a dead inbound watcher, a stale inbound backlog, a stopped
    ops dispatcher and a held retention error -- none of it visible before, all of it exit 0."""
    health = (200, {"ok": True, "version": "1.2.3", "at": "2026-09-23T12:00:00Z",
                    "rail": {"number": None, "driver": {"kind": "adb"}},
                    "queue": {"sent": 41}, "quota": {"sent_today": 0},
                    "watcher": {"alive": False, "last_ok_at": "2026-09-23T11:00:00Z"},
                    "ops_dispatcher": {"alive": False, "last_ok_at": "2026-09-23T11:00:00Z"},
                    "phone_ops": {"queued": 5, "oldest_queued_at": "2026-09-23T10:00:00Z"},
                    "inbound": {"unacked": 3, "oldest_unacked_at": "2026-09-23T11:00:00Z"},
                    "retention": {"last_ok_at": "2026-09-22T09:00:00Z", "errors": 2,
                                  "last_error": "boom", "last_error_at": "2026-09-23T08:00:00Z"}})
    assert run(["health"], now=HEALTH_NOW, v1_health=health)[0] == 1
    out = capsys.readouterr().out
    assert "watcher: alive=False  last_ok_at='2026-09-23T11:00:00Z'  age=3600s" in out
    assert "ops_dispatcher: alive=False  last_ok_at='2026-09-23T11:00:00Z'" in out
    assert "phone_ops: queued=5  oldest_queued_at='2026-09-23T10:00:00Z'" in out
    assert "inbound: unacked=3  oldest_unacked_at='2026-09-23T11:00:00Z'  age=3600s" in out
    assert "retention: last_ok_at='2026-09-22T09:00:00Z'  errors=2  last_error='boom' at '2026-09-23T08:00:00Z'" in out
    assert "ATTENTION: watcher.alive is false; watcher.last_ok_at is 3600s old (over 60s); " \
           "ops_dispatcher.alive is false; oldest unacked inbound row is 3600s old (over 60s); " \
           "retention has 2 error(s)" in out


# --- a run that is not open is not a run to keep asking about ----------------------------------------

def stopped_run(queued=2):
    items = [{"client_msg_id": "k0", "position": 0, "thread": "…0", "status": "sent",
              "code": None, "detail": "Zugestellt", "attempts": 1, "next_attempt_at": None}]
    items += [{"client_msg_id": f"k{i + 1}", "position": i + 1, "thread": f"…{i + 1}",
               "status": "queued", "code": None, "detail": None, "attempts": 0,
               "next_attempt_at": None} for i in range(queued)]
    return (200, {"ok": True, "run": {"run_id": "b1", "state": "stopped"},
                  "counts": {"sent": 1, "queued": queued}, "items": items})


def test_queued_items_on_a_stopped_run_are_not_reported_as_on_their_way(capsys):
    """The executor's runner selects due items out of OPEN runs only, so these items will never be
    attempted and the status will never change however often it is asked."""
    code, _ = run(["broadcast", "--id", "b1", "--stop"], **{"v1_broadcasts/b1/stop": stopped_run()})
    out = capsys.readouterr().out
    assert code == 1, "queued items on a stopped run are something to look at"
    assert "never attempted" in out and "--status" not in out


def test_queued_items_on_an_open_run_still_say_ask_again(capsys):
    view = (200, {"ok": True, "run": {"run_id": "b1", "state": "open"}, "counts": {"queued": 1},
                  "items": [{"client_msg_id": "k1", "position": 0, "thread": "…1",
                             "status": "queued", "code": None, "detail": None, "attempts": 0,
                             "next_attempt_at": None}]})
    code, _ = run(["broadcast", "--id", "b1", "--status"], **{"v1_broadcasts/b1": view})
    assert code == 3 and "--status" in capsys.readouterr().out


def test_a_pacing_argument_that_is_not_an_object_is_a_usage_error(tmp_path, capsys, autosend):
    file = recipients(tmp_path, f"phone\n{MINE}\n")
    code, fake = run(["broadcast", "--id", "b1", "--file", file, "--body", "Hallo",
                      "--pacing", "[1,2]", "--send"])
    assert code == 2 and fake.calls == []
    assert "--pacing must be a JSON object" in capsys.readouterr().err


# --- --status records a sent broadcast into wa_messages (TASK-284) -------------------------------
# Before this, the brain had no memory of a template a phone-rail broadcast just sent: turn_context()
# reads only wa_messages, and a broadcast send never wrote one -- see cmd_broadcast's own module
# docstring, "THE BRAIN'S MEMORY OF A BROADCAST".

SENT_AT = "2026-09-23T19:21:19.675Z"     # stands in for the real send moment, far from "now"


def _sent_view(key, body, updated_at=SENT_AT):
    return (200, {"ok": True, "run": {"run_id": "b1", "state": "done"}, "counts": {"sent": 1},
                  "items": [{"client_msg_id": key, "position": 0, "thread": "…0", "status": "sent",
                             "code": None, "detail": "Zugestellt", "attempts": 1,
                             "next_attempt_at": None, "body_sha256": CLI.D.body_sha256(body),
                             "updated_at": updated_at}]})


def test_a_status_poll_without_file_leaves_wa_messages_untouched_and_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    key = BI.campaign_key(campaign_id="b1", phone=PARTNER, attempt=1)
    code, _ = run(["broadcast", "--id", "b1", "--status"], **{"v1_broadcasts/b1": _sent_view(key, "Hallo")})
    assert code == 0
    assert "were not recorded into wa_messages" in capsys.readouterr().out
    assert ST.message_by_wamid(ST.db(), key) is None


def test_a_status_poll_with_file_records_the_sent_item_into_wa_messages(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    file = recipients(tmp_path, f"phone\n{PARTNER}\n")
    key = BI.campaign_key(campaign_id="b1", phone=PARTNER, attempt=1)
    code, _ = run(["broadcast", "--id", "b1", "--status", "--file", file, "--body", "Hallo"],
                  **{"v1_broadcasts/b1": _sent_view(key, "Hallo")})
    assert code == 0
    assert "1 sent item(s) newly recorded" in capsys.readouterr().out
    row = ST.message_by_wamid(ST.db(), key)
    assert row["phone"] == PARTNER and row["body"] == "Hallo" and row["direction"] == "out"
    assert row["kind"] == "text" and row["meta"] == {"action": "broadcast", "run_id": "b1"}
    assert row["at"] == SENT_AT, (
        "stamped at the real send moment (the item's own updated_at), not at poll time -- a "
        "human runs --status well after the send, and 'now' would insert this row out of "
        "chronological order against whatever the candidate said in between (2026-09-23 review)")


def test_a_repeat_status_poll_does_not_duplicate_the_recorded_row(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    file = recipients(tmp_path, f"phone\n{PARTNER}\n")
    key = BI.campaign_key(campaign_id="b1", phone=PARTNER, attempt=1)
    view = _sent_view(key, "Hallo")
    run(["broadcast", "--id", "b1", "--status", "--file", file, "--body", "Hallo"],
        **{"v1_broadcasts/b1": view})
    code, _ = run(["broadcast", "--id", "b1", "--status", "--file", file, "--body", "Hallo"],
                  **{"v1_broadcasts/b1": view})
    assert code == 0
    assert "1 already there" in capsys.readouterr().out
    assert len(ST.messages_for(ST.db(), PARTNER, direction="out")) == 1


def test_a_status_poll_with_a_different_body_than_what_was_sent_is_refused(tmp_path, monkeypatch, capsys):
    """The run view's body_sha256 is the sent item's real identity; a --body that rebuilds a
    different text for the same key is not this run's own pairing and is refused, not recorded."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    file = recipients(tmp_path, f"phone\n{PARTNER}\n")
    key = BI.campaign_key(campaign_id="b1", phone=PARTNER, attempt=1)
    code, _ = run(["broadcast", "--id", "b1", "--status", "--file", file, "--body", "A different text"],
                  **{"v1_broadcasts/b1": _sent_view(key, "Hallo")})
    assert code == 2
    assert "whose body does not match" in capsys.readouterr().err
    assert ST.message_by_wamid(ST.db(), key) is None


def test_a_sent_item_the_file_cannot_explain_at_all_is_refused_not_silent(tmp_path, monkeypatch, capsys):
    """2026-09-23 review: this used to `continue` quietly on a key the file's recipients never
    produce -- a wrong --file (typo'd path, an old recipients list, the wrong --attempt) then
    matched nothing, printed nothing, and exited 0, indistinguishable from a clean "already
    recorded". A sent item this file cannot account for is exactly as loud a problem as a body
    mismatch: it means this was not really the file/attempt this run went out from."""
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    file = recipients(tmp_path, f"phone\n{MINE}\n")            # a DIFFERENT phone than the run sent
    key = BI.campaign_key(campaign_id="b1", phone=PARTNER, attempt=1)
    code, _ = run(["broadcast", "--id", "b1", "--status", "--file", file, "--body", "Hallo"],
                  **{"v1_broadcasts/b1": _sent_view(key, "Hallo")})
    assert code == 2
    assert "cannot explain at all" in capsys.readouterr().err
    assert ST.message_by_wamid(ST.db(), key) is None


# --- the human escape hatch (TASK-131 round 5, decision-9 2026-09-22) ----------------------------------
UNRESOLVED = (200, {"ok": True, "at": "2026-09-22T10:00:00.000Z", "count": 1, "files": [
    {"queue_id": "wab.q.aaaaaaaaaaaaaaaaaaaa", "media_id": "wab.m.aaaaaaaaaaaaaaaaaaaa",
     "kind": "document", "size": 40213, "source_dir": "WhatsApp Documents",
     "pulled_at": "2026-09-22T09:58:00.000Z", "age_sec": 120.0, "related_threads": []}]})


def test_media_list_prints_the_facts_and_only_the_facts(capsys):
    code, _ = run(["media-list"], v1_media=UNRESOLVED)
    out = capsys.readouterr().out
    assert code == 0
    assert "wab.q.aaaaaaaaaaaaaaaaaaaa" in out
    assert "kind=document" in out and "size=40213B" in out
    assert "folder='WhatsApp Documents'" in out
    # PII: nothing this listing was never handed (no filename, no phone) can appear in it
    assert "phone" not in out.lower()


def test_media_list_json_passes_the_executors_answer_through(capsys):
    code, _ = run(["media-list", "--json"], v1_media=UNRESOLVED)
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out == UNRESOLVED[1]["files"]


def test_media_list_flags_a_duplicate_pull_out_loud(capsys):
    dup = (200, {"ok": True, "at": "2026-09-22T10:00:00.000Z", "count": 1, "files": [
        {"queue_id": "wab.q.bbbbbbbbbbbbbbbbbbbb", "media_id": "wab.m.bbbbbbbbbbbbbbbbbbbb",
         "kind": "document", "size": 40213, "source_dir": "WhatsApp Documents",
         "pulled_at": "2026-09-22T09:58:00.000Z", "age_sec": 120.0, "related_threads": [],
         "content_pull_count": 2}]})
    code, _ = run(["media-list"], v1_media=dup)
    out = capsys.readouterr().out
    assert code == 0 and "DUPLICATE_CONTENT(x2)" in out


def test_media_attach_posts_the_id_and_the_operators_own_phone(capsys):
    report = (200, {"ok": True, "queue_id": "wab.q.aaaa", "media_id": "wab.m.aaaa",
                    "kind": "document", "thread": "deadbeef1234"})
    code, fake = run(["media-attach", "--id", "wab.q.aaaa", "--phone", PARTNER],
                     **{"v1_media_attach": report})
    assert code == 0
    posted = fake.posted(BR.MEDIA_ATTACH_PATH)
    assert len(posted) == 1
    assert posted[0]["body"] == {"queue_id": "wab.q.aaaa", "phone": PARTNER}
    out = capsys.readouterr().out
    assert "attached wab.q.aaaa" in out and "deadbeef1234" in out
    assert PARTNER not in out, "the report carries a thread tag, never the number itself"


def test_media_attach_an_unknown_id_is_a_refusal(capsys):
    refusal = BR.BridgeError("no queued file with this id", status_code=404,
                             code="media_not_found")
    code, _ = run(["media-attach", "--id", "wab.q.nope", "--phone", PARTNER],
                  **{"v1_media_attach": refusal})
    assert code == 1
    assert "media_not_found" in capsys.readouterr().err


def test_media_attach_twice_is_a_refusal(capsys):
    refusal = BR.BridgeError("wab.q.aaaa is already attached", status_code=409,
                             code="already_attached")
    code, _ = run(["media-attach", "--id", "wab.q.aaaa", "--phone", PARTNER],
                  **{"v1_media_attach": refusal})
    assert code == 1
    assert "already_attached" in capsys.readouterr().err


def test_media_attach_needs_both_id_and_phone():
    with pytest.raises(SystemExit) as caught:
        run(["media-attach", "--id", "wab.q.aaaa"])
    assert caught.value.code == 2


# --- TASK-261: the rows a reconcile still has to answer for, read-only -----------------------------
UNRESOLVED_SENDS = (200, {"ok": True, "at": "2026-09-22T10:00:00.000Z", "count": 1, "rows": [
    {"client_msg_id": "wab.o.aaaa", "thread_tag": "deadbeef1234", "state": "unconfirmed",
     "age_sec": 518400.0}]})


def test_unresolved_list_prints_id_state_thread_and_age(capsys):
    code, _ = run(["unresolved-list"], v1_unresolved=UNRESOLVED_SENDS)
    out = capsys.readouterr().out
    assert code == 0
    assert "wab.o.aaaa" in out
    assert "state=unconfirmed" in out and "thread=deadbeef1234" in out and "age=518400s" in out
    assert "reconcile --keys wab.o.aaaa" in out, "feeds straight into reconcile"


def test_unresolved_list_json_passes_the_executors_answer_through(capsys):
    code, _ = run(["unresolved-list", "--json"], v1_unresolved=UNRESOLVED_SENDS)
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out == UNRESOLVED_SENDS[1]["rows"]


def test_unresolved_list_with_nothing_stuck_prints_no_reconcile_hint(capsys):
    empty = (200, {"ok": True, "at": "2026-09-22T10:00:00.000Z", "count": 0, "rows": []})
    code, _ = run(["unresolved-list"], v1_unresolved=empty)
    out = capsys.readouterr().out
    assert code == 0
    assert "0 unresolved send(s)" in out
    assert "reconcile --keys" not in out


# --- TASK-230: reconcile / ops-resolve --------------------------------------------------------

def test_reconcile_prints_each_keys_verdict(capsys):
    verdicts = [{"client_msg_id": "wab.o.x", "verdict": "confirmed_sent",
                "evidence": "ledger: delivery tick read at send time"}]
    code, fake = run(["reconcile", "--keys", "wab.o.x"], **{"v1_reconcile": (200, verdicts)})
    assert code == 0
    out = capsys.readouterr().out
    assert "wab.o.x" in out and "confirmed_sent" in out
    assert fake.calls[0]["body"] == {"client_msg_ids": ["wab.o.x"]}


def test_reconcile_json_passes_the_verdict_list_through(capsys):
    verdicts = [{"client_msg_id": "wab.o.x", "verdict": "indeterminate"}]
    code, _fake = run(["reconcile", "--keys", "wab.o.x", "--json"],
                      **{"v1_reconcile": (200, verdicts)})
    assert code == 0
    assert json.loads(capsys.readouterr().out) == verdicts


def test_reconcile_splits_and_trims_comma_separated_keys(capsys):
    code, fake = run(["reconcile", "--keys", " wab.o.x , wab.o.y "],
                     **{"v1_reconcile": (200, [])})
    assert code == 0
    assert fake.calls[0]["body"] == {"client_msg_ids": ["wab.o.x", "wab.o.y"]}


def test_reconcile_needs_at_least_one_key():
    code, fake = run(["reconcile", "--keys", " , "])
    assert code == CLI.EXIT_CONFIG
    assert fake.calls == []


def test_ops_resolve_prints_confirmation(capsys):
    op_id = "op." + "5" * 24
    fake = FakeBridge()
    fake.answers[f"/v1/ops/{op_id}/resolve"] = (200, {"ok": True, "op_id": op_id, "resolved": True})
    client = BR.Client(transport=fake, base_url=BASE, token="tok")
    code = CLI.main(["ops-resolve", "--id", op_id], client=client)
    assert code == 0
    assert f"resolved {op_id}" in capsys.readouterr().out
    assert fake.calls[0]["method"] == "POST"


def test_ops_resolve_json_passes_the_report_through(capsys):
    op_id = "op." + "6" * 24
    fake = FakeBridge()
    fake.answers[f"/v1/ops/{op_id}/resolve"] = (200, {"ok": True, "op_id": op_id, "resolved": True})
    client = BR.Client(transport=fake, base_url=BASE, token="tok")
    code = CLI.main(["ops-resolve", "--id", op_id, "--json"], client=client)
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "op_id": op_id, "resolved": True}


def test_ops_resolve_an_unknown_op_id_is_a_refusal(capsys):
    op_id = "op." + "7" * 24
    fake = FakeBridge()
    fake.answers[f"/v1/ops/{op_id}/resolve"] = BR.BridgeError(
        f"no such op {op_id!r}", status_code=404, code="op_not_found")
    client = BR.Client(transport=fake, base_url=BASE, token="tok")
    code = CLI.main(["ops-resolve", "--id", op_id], client=client)
    assert code == 1
    assert "op_not_found" in capsys.readouterr().err
