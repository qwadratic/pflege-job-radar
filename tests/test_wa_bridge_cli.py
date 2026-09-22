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


def destroyed(operation, verified=True, **extra):
    """What bridge/operations.py answers when a destruction went through and it proved it."""
    return 200, {"ok": True, "operation": operation, "chat": {"title": "Ivan Test", "phone": MINE},
                 "destroyed": {"visible_messages": 12, "visibility": "visible_without_scrolling"},
                 "ui": {"taps": ["menu", "clear"]},
                 "verification": {"verified": verified, "method": "reopen_and_count", "why": ""},
                 "audit_id": 41, **extra}


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


# The moment every audit-aware test pretends to be calling at: one minute before the row above.
ASKED_AT = datetime(2026, 9, 21, 17, 25, tzinfo=timezone.utc)
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


# --- destroying a chat ------------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["clear-chat", "delete-chat"])
def test_a_destructive_command_shows_the_chat_and_destroys_nothing_without_confirm(command, capsys):
    code, fake = run([command, "--phone", MINE], v1_chats=CHATS, v1_thread=THREAD)
    out = capsys.readouterr().out
    assert code == 0 and [c["method"] for c in fake.calls] == ["GET", "GET"], "it read the list and the chat"
    assert "Ivan Test" in out and "2 message(s)" in out, "what is about to be destroyed is printed first"
    assert "--confirm" in out and "--expect-messages 2" in out, "the preview hands over the count to assert"
    assert fake.calls[1]["query"].endswith("include_text=0"), "a preview needs the count, not the bodies"


@pytest.mark.parametrize("command,path,operation", [("clear-chat", BR.CHAT_CLEAR_PATH, "clear_chat"),
                                                    ("delete-chat", BR.CHAT_DELETE_PATH, "delete_chat")])
def test_a_confirmed_destruction_names_the_row_it_just_read(command, path, operation, capsys):
    code, fake = run([command, "--phone", MINE, "--confirm", "--expect-messages", "12"],
                     v1_chats=CHATS, **{path[1:].replace("/", "_"): destroyed(operation)})
    posted = fake.posted(path)
    assert code == 0 and len(posted) == 1
    assert posted[0]["body"]["confirm"] is True
    assert posted[0]["body"]["chat"] == "Ivan Test", "the row's own title is the identity the handset offers"
    assert posted[0]["body"]["phone"] == MINE, "and its resolved number, which the executor checks the header against"
    assert posted[0]["body"]["expect_messages"] == 12, "a conversation that moved on is not the approved one"
    assert "verification" in capsys.readouterr().out


def test_a_destruction_the_executor_does_not_verify_fails_loudly(capsys):
    code, fake = run(["delete-chat", "--phone", MINE, "--confirm"], v1_chats=CHATS,
                     v1_chats_delete=destroyed("delete_chat", verified=False))
    assert code == 1 and "unconfirmed" in capsys.readouterr().err, "a 200 is not proof; the verification is"
    assert len(fake.posted(BR.CHAT_DELETE_PATH)) == 1


def test_a_clear_that_comes_back_as_a_delete_is_not_accepted(capsys):
    code, _ = run(["clear-chat", "--phone", MINE, "--confirm"], v1_chats=CHATS,
                  v1_chats_clear=destroyed("delete_chat"))
    assert code == 1, "clearing a chat and removing it are different destructions"
    assert "delete_chat" in capsys.readouterr().err


def test_an_ambiguous_display_name_is_never_destroyed(capsys):
    chats = (200, {"ok": True, "chats": [{"title": "Ivan Test", "phone": None, "ambiguous": True,
                                          "phone_source": "ambiguous_display_name", "archived": False}]})
    code, fake = run(["delete-chat", "--title", "Ivan Test", "--confirm"], v1_chats=chats)
    assert code == 2 and fake.posted(BR.CHAT_DELETE_PATH) == []
    assert "more than one number" in capsys.readouterr().err


def test_a_chat_the_handset_does_not_show_is_never_destroyed(capsys):
    """A title nobody ever had is still an error, and it says the record was consulted -- otherwise
    "no chat on the handset" carries the same weight whether or not we are the reason."""
    code, fake = run(["delete-chat", "--phone", "+4915550009999", "--confirm"], now=ASKED_AT,
                     v1_chats=CHATS, v1_audit=audit())
    assert code == 2 and fake.posted(BR.CHAT_DELETE_PATH) == []
    assert ("ERROR: no chat on the handset for +4915550009999 -- nothing was touched. Run `chats` "
            "to see the list. The executor's audit records no destruction of it either"
            ) in capsys.readouterr().err


def test_naming_the_chat_twice_or_not_at_all_is_refused(capsys):
    assert run(["delete-chat", "--confirm"])[0] == 2
    assert run(["delete-chat", "--phone", MINE, "--title", "Ivan Test", "--confirm"])[0] == 2
    assert "exactly one" in capsys.readouterr().err, "a number and a title can name two different chats"


def test_a_chat_named_by_its_title_is_the_row_that_title_drew(capsys):
    code, fake = run(["delete-chat", "--title", "Partner Test", "--confirm"],
                     v1_chats=CHATS, v1_chats_delete=destroyed("delete_chat"))
    posted = fake.posted(BR.CHAT_DELETE_PATH)
    assert code == 0 and posted[0]["body"]["chat"] == "Partner Test"
    assert posted[0]["body"]["phone"] == PARTNER, "the row's own number travels with it"


def test_the_client_refuses_a_destruction_before_any_post():
    """The CLI is not the only caller: the guard lives in the client, where a model calling the
    method directly meets it too."""
    fake = FakeBridge()
    client = BR.Client(transport=fake, base_url=BASE, token="tok")
    with pytest.raises(BR.BridgeError) as no_confirm:
        client.delete_chat(chat="Ivan Test")
    with pytest.raises(ValueError) as no_chat:
        client.delete_chat(chat="  ", confirm=True)
    assert no_confirm.value.status_code == BR.CONTRACT_STATUS and "confirm" in str(no_confirm.value)
    assert "chat" in str(no_chat.value), "a destruction with no chat named never becomes a request"
    assert fake.calls == [], "neither refusal touched the phone"


# --- a destruction whose ANSWER was lost (2026-09-21) -------------------------------------------------
# The incident these are written from: `delete-chat --title "+43 670 4048778" --confirm` printed
# "bridge did not answer within 90s: the executor may still be sending", the operator read it as
# "nothing happened" and ran it again, the second run said "no chat on the handset for that title",
# and he concluded the tool had matched the wrong chat. It had deleted the right one, twice over:
# the first call finished the job and the audit row proves it. Both sentences are now answerable
# from the record, and both are asserted here as the operator reads them.

def test_a_destruction_whose_answer_was_lost_reports_what_the_audit_proves(capsys):
    code, fake = run(["delete-chat", "--phone", MINE, "--confirm", "--expect-messages", "3"],
                     now=ASKED_AT, v1_chats=CHATS, v1_chats_delete=TIMED_OUT,
                     v1_audit=audit(audit_row()))
    out = capsys.readouterr().out
    assert code == 0, "the chat was destroyed and proved destroyed: that is not a failure"
    assert ("NOTE: the bridge answer was lost (bridge did not answer within 222.0s: the executor "
            "may still be working), and the executor's audit says this delete_chat finished and "
            "was verified at 2026-09-21T17:26:16.912Z (audit 3). The destruction below is the "
            "record, not the answer") in out
    assert '"visible_messages": 3' in out and "audit=3" in out, "what was destroyed comes from the row"
    assert len(fake.posted(BR.CHAT_DELETE_PATH)) == 1, "the destruction was attempted exactly once"


def test_a_destruction_whose_answer_was_lost_before_it_began_says_nothing_was_destroyed(capsys):
    """The other half of the same question. The audit row is written BEFORE the first tap, so no
    row means no tap had happened when we looked -- and the moment we looked is said out loud,
    because the executor may still hold the phone."""
    code, _ = run(["delete-chat", "--phone", MINE, "--confirm"], now=ASKED_AT,
                  v1_chats=CHATS, v1_chats_delete=TIMED_OUT, v1_audit=audit())
    err = capsys.readouterr().err
    assert code == 1
    assert ("ERROR: the bridge answer was lost (bridge did not answer within 222.0s: the executor "
            "may still be working) and the executor's audit records no delete_chat of 'Ivan Test' "
            "since 2026-09-21T17:25:00.000Z: nothing had been destroyed as of "
            "2026-09-21T17:25:00.000Z. The audit row is written before the first tap, so if the "
            "executor is still working one will appear -- check with: tools/wa_bridge.py audit "
            "--title 'Ivan Test'") in err


def test_a_destruction_whose_answer_was_lost_mid_verb_is_neither_done_nor_untouched(capsys):
    """A row that started and never finished: the handset was tapped and nothing proved it. This is
    the one state where the honest answer is still a question, and it names which one."""
    unproved = audit_row(verified=False,
                         detail={"state": "unproved",
                                 "destroyed": {"visible_messages": 3},
                                 "proof": {"verified": False, "method": "chat_list_rescan",
                                           "chat_present": None,
                                           "why": "the verification could not run: adb died"}})
    code, _ = run(["delete-chat", "--phone", MINE, "--confirm"], now=ASKED_AT,
                  v1_chats=CHATS, v1_chats_delete=TIMED_OUT, v1_audit=audit(unproved))
    err = capsys.readouterr().err
    assert code == 1
    assert ("and the audit says this delete_chat of 'Ivan Test' DID start at "
            "2026-09-21T17:26:16.912Z (audit 3, state 'unproved'): the handset was tapped and the "
            "result is not proved. Read the list with: tools/wa_bridge.py chats") in err


def test_an_audit_row_from_before_this_call_is_not_this_calls_outcome(capsys):
    """Yesterday's deletion of a chat with the same title says nothing about the call that just
    timed out. The comparison is a moment, not a string: the ledger writes milliseconds and this
    side holds microseconds."""
    old = audit_row(id=1, at="2026-09-20T09:00:00.000Z")
    code, _ = run(["delete-chat", "--phone", MINE, "--confirm"], now=ASKED_AT,
                  v1_chats=CHATS, v1_chats_delete=TIMED_OUT, v1_audit=audit(old))
    assert code == 1 and "records no delete_chat of 'Ivan Test'" in capsys.readouterr().err


# --- asking again for a chat this rail already deleted ------------------------------------------------

GONE = "+43 670 4048778"
DELETED_GONE = audit_row(chat_title=GONE, to_phone="+436704048778")


def test_a_repeat_delete_of_a_chat_we_destroyed_is_reported_as_done(capsys):
    """Ivan's second command. "no chat on the handset for that title" was true and useless: the
    reason it is not there is that the first command removed it, which is what he asked for."""
    code, fake = run(["delete-chat", "--title", GONE, "--confirm"], now=ASKED_AT,
                     v1_chats=CHATS, v1_audit=audit(DELETED_GONE))
    out = capsys.readouterr().out
    assert code == 0, "the handset is in the state the operator asked for"
    assert fake.posted(BR.CHAT_DELETE_PATH) == [], "and nothing was touched to find that out"
    assert ("already deleted: title '+43 670 4048778' was removed by this rail at "
            "2026-09-21T17:26:16.912Z (audit 3, verified=True) and is not on the handset's list "
            "now. Nothing was touched") in out
    assert '"visible_messages": 3' in out, "what it held when it went is in the record"


def test_a_repeat_clear_of_a_chat_we_deleted_is_not_reported_as_done(capsys):
    """The mirror, and it is not symmetric: clear-chat promises to empty a conversation and KEEP
    it. A deleted chat cannot be emptied, so the operator's intent was not met."""
    code, fake = run(["clear-chat", "--title", GONE, "--confirm"], now=ASKED_AT,
                     v1_chats=CHATS, v1_audit=audit(DELETED_GONE))
    err = capsys.readouterr().err
    assert code == 1 and fake.posted(BR.CHAT_CLEAR_PATH) == []
    assert ("ERROR: no chat on the handset for title '+43 670 4048778' because this rail DELETED "
            "it at 2026-09-21T17:26:16.912Z (audit 3) -- clear-chat empties a chat and keeps it, "
            "and there is no chat left to empty. Nothing was touched") in err


# The live ledger's first row, verbatim off the mini on 2026-09-21: a VERIFIED delete of five
# messages whose ``to_phone`` is null, because the handset could not resolve that saved contact to
# a number. Null is a normal state of this column, not a gap, and a lookup that treats it as "not
# this chat" says the opposite of what the record holds.
UNNUMBERED = audit_row(
    id=1, at="2026-09-21T17:21:49.205Z", chat_title="Valentyn NDT", chat_tag="5e0b05f22cbb",
    to_phone=None,
    detail={"state": "verified",
            "destroyed": {"incoming": 4, "outgoing": 1, "visible_messages": 5,
                          "visibility": "visible_without_scrolling"},
            "proof": {"verified": True, "method": "chat_list_rescan", "chat_present": False,
                      "chats_on_list": 9, "why": ""}})


def test_a_delete_of_a_chat_whose_row_carries_no_number_is_an_open_question(capsys):
    """Ivan's sentence, restored for the other way of naming a chat. A --phone lookup used to drop
    every row with a null number, so the one command that could have answered "did we destroy it"
    answered "we never did" about a destruction this rail had proved."""
    code, fake = run(["delete-chat", "--phone", "+4915550000007", "--confirm"], now=ASKED_AT,
                     v1_chats=CHATS, v1_audit=audit(UNNUMBERED))
    err = capsys.readouterr().err
    assert code == 1, "not 2: 'a typo or a wrong list' is a verdict this record does not support"
    assert fake.posted(BR.CHAT_DELETE_PATH) == [], "and nothing was touched to find that out"
    assert ("ERROR: no chat on the handset for +4915550000007 -- nothing was touched. Run `chats` "
            "to see the list. Whether this rail destroyed it is NOT KNOWN from here: the audit "
            "holds 1 delete_chat row(s) whose number the handset never resolved, so none of them "
            "can be tied to +4915550000007 or ruled out -- audit 1 'Valentyn NDT' at "
            "2026-09-21T17:21:49.205Z (verified=True). Check with: tools/wa_bridge.py audit") in err


def test_a_phone_lookup_still_reports_the_destruction_the_record_ties_to_that_number(capsys):
    """The other half: a row whose number IS the one named answers the question outright, with the
    title it wore, and an unnumbered row sitting beside it changes nothing."""
    code, _ = run(["delete-chat", "--phone", "+436704048778", "--confirm"], now=ASKED_AT,
                  v1_chats=CHATS, v1_audit=audit(DELETED_GONE, UNNUMBERED))
    out = capsys.readouterr().out
    assert code == 0
    assert ("already deleted: +436704048778 (the row the audit titles '+43 670 4048778') was "
            "removed by this rail at 2026-09-21T17:26:16.912Z (audit 3, verified=True) and is not "
            "on the handset's list now. Nothing was touched") in out


def test_an_audit_that_will_not_read_does_not_erase_what_the_chat_list_established(capsys):
    """The executor ANSWERED: the list it drew is how we know the chat is missing, and only the
    audit read died. Letting that escape printed "the executor never answered" -- and said nothing
    at all about the chat a destructive command had just been pointed at."""
    for failure in (BR.BridgeUnreachable("bridge unreachable: [Errno 111] Connection refused"),
                    BR.BridgeError("bridge HTTP 500", status_code=500, code=None,
                                   payload={"error": {"code": "internal"}})):
        code, fake = run(["delete-chat", "--title", GONE, "--confirm"], now=ASKED_AT,
                         v1_chats=CHATS, v1_audit=failure)
        err = capsys.readouterr().err
        assert code == 1 and fake.posted(BR.CHAT_DELETE_PATH) == []
        assert (f"ERROR: no chat on the handset for title '{GONE}' -- nothing was touched. Run "
                f"`chats` to see the list. Whether this rail destroyed it is NOT KNOWN from here: "
                f"the executor's audit could not be read ({failure}). Check with: "
                f"tools/wa_bridge.py audit") in err
        # "Connection refused" is the operating system's own words about the audit socket and is
        # quoted as such; what may not appear is this rail calling the executor's answer a refusal.
        assert "bridge refused" not in err and "never answered" not in err


def test_the_record_cannot_confirm_the_archived_flag_and_says_so(capsys):
    """--archived is an assertion about which folder the chat was in, and the audit has no folder
    column to check it against. Nothing was touched either way and the chat is on neither list, so
    this still reads as done -- what it may not do is call a row it cannot place the answer."""
    code, _ = run(["delete-chat", "--title", GONE, "--archived", "--confirm"], now=ASKED_AT,
                  v1_chats=CHATS, v1_audit=audit(DELETED_GONE))
    out = capsys.readouterr().out
    assert code == 0
    assert ("Nothing was touched. The audit records no folder, so that row cannot confirm the chat "
            "it is about was the archived one you named") in out


def not_found(**record):
    """The executor's 404 for a title on neither list: the row it found, and its own verdict on
    whether that row is about the number the caller named (bridge/operations.py::_not_found)."""
    return BR.BridgeError("bridge HTTP 404", status_code=404, code="chat_not_found", payload={
        "ok": False, "error": {"code": "chat_not_found", "detail": {
            "chat_tag": "3a8400e42ee6", "chats_on_list": 7,
            "destroyed_by_us": {"audit_id": 3, "at": "2026-09-21T17:26:16.912Z",
                                "operation": "delete_chat", "verified": True,
                                "detail": DELETED_GONE["detail"], **record}}}})


@pytest.mark.parametrize("verdict,why", [
    (BR.NUMBER_DIFFERENT, f"the executor says that row is about a different number than {MINE}, "
                          f"and two contacts can share one display name"),
    (BR.NUMBER_UNRECORDED, "the handset never resolved that row to a number, so the record cannot "
                           "say whose conversation it was"),
    (None, "the executor said nothing about whose number that row is (named_number=None)")])
def test_a_record_about_another_number_is_not_an_answer_about_this_chat(verdict, why):
    """A title is not an identity: two contacts can wear one display name, and everywhere else in
    this package that is a refusal. A row found by title alone therefore answers about the caller's
    conversation only when the executor says the numbers are the same -- and when it says otherwise,
    or says nothing (an executor that predates the comparison), the honest answer is the question."""
    fake = FakeBridge(v1_chats_delete=not_found(named_number=verdict))
    client = BR.Client(transport=fake, base_url=BASE, token="tok")
    with pytest.raises(BR.BridgeError) as caught:
        client.delete_chat(chat=GONE, phone=MINE, confirm=True)
    assert caught.value.code == BR.CODE_DESTROY_OUTCOME_UNKNOWN
    assert str(caught.value) == (
        f"no chat titled {GONE!r} is on the handset's list, and this rail's record cannot be tied "
        f"to {MINE}: it holds a delete_chat of that title at 2026-09-21T17:26:16.912Z (audit 3) "
        f"and {why}. Whether {MINE}'s conversation was destroyed is NOT KNOWN from here. Check "
        f"with: tools/wa_bridge.py audit --title {GONE!r}")
    assert caught.value.code in BR.LOST_ANSWER_CODES, "so the CLI prints it without 'refused'"


def test_an_unproved_row_is_not_upgraded_into_a_proof_by_todays_absence():
    """The absence is this call's own scan and is real. Whether the recorded destruction was ever
    verified is a different question with its own answer in the row, and overwriting it made a
    delete that never proved anything read as proved."""
    unproved = {"state": "unproved", "destroyed": {"visible_messages": 940},
                "proof": {"verified": False, "method": "none", "chat_present": None,
                          "why": "the driver raised after the operation began"}}
    fake = FakeBridge(v1_chats_delete=not_found(named_number=BR.NUMBER_SAME, verified=False,
                                                detail=unproved))
    report = BR.Client(transport=fake, base_url=BASE, token="tok").delete_chat(
        chat=GONE, phone=MINE, confirm=True)
    assert report["verification"]["audit_row"] == {"verified": False, "state": "unproved"}
    assert report["verification"]["proves"] == ("the chat is absent from both folders now, not "
                                                "that the recorded destruction was verified when "
                                                "it ran")
    assert "verified=False" in report["note"], "and the row's own verdict is in the sentence"
    assert report["chat"]["phone"] is None, "the executor keeps the number out of that answer, and "\
                                            "filling it in from the argument would forge the match"


def test_the_executors_own_504_is_never_printed_as_a_refusal(capsys):
    """The most frequent way a destruction ends badly on this handset: the taps landed and the
    chat list would not come to the front to prove it. "bridge refused" over "was confirmed on the
    handset" is the 2026-09-21 lie written in the executor's vocabulary instead of ours."""
    unverified = BR.BridgeError(
        "delete_chat was confirmed on the handset and the result could not be proved: the chat "
        "list would not come to the front", status_code=504, code="destruction_unverified",
        payload={"ok": False, "error": {"code": "destruction_unverified", "http_status": 504,
                                        "detail": {"audit_id": 7, "chat_tag": "3a84"}}})
    code, _ = run(["delete-chat", "--phone", MINE, "--confirm"], v1_chats=CHATS,
                  v1_chats_delete=unverified)
    err = capsys.readouterr().err
    assert code == 1 and "refused" not in err
    assert ("ERROR: the handset was touched and the result is not proved -- this is not a refusal "
            "(status 504, code 'destruction_unverified'): delete_chat was confirmed on the handset "
            "and the result could not be proved: the chat list would not come to the front "
            "(audit 7). Read what the handset shows: tools/wa_bridge.py chats") in err


def test_the_executors_own_not_found_carries_the_reason_and_the_client_reads_it():
    """The CLI checks the list first, so this 404 is what a MODEL calling the client directly meets
    when the row went away between the list and the call. The reason travels in the refusal
    (bridge/operations.py::_match_row), so no second round trip is needed to tell the two apart."""
    refusal = BR.BridgeError("bridge HTTP 404", status_code=404, code="chat_not_found", payload={
        "ok": False, "error": {"code": "chat_not_found", "message": "no chat with that title is on "
                               "the handset's list: this executor deleted it at "
                               "2026-09-21T17:26:16.912Z (audit 3)",
                               "detail": {"chat_tag": "3a8400e42ee6", "chats_on_list": 7,
                                          "destroyed_by_us": {
                                              "audit_id": 3, "at": "2026-09-21T17:26:16.912Z",
                                              "operation": "delete_chat", "verified": True,
                                              "detail": DELETED_GONE["detail"]}}}})
    fake = FakeBridge(v1_chats_delete=refusal, v1_chats_clear=refusal)
    client = BR.Client(transport=fake, base_url=BASE, token="tok")
    report = client.delete_chat(chat=GONE, confirm=True)
    assert report["reported_by"] == BR.FROM_AUDIT_ALREADY_GONE
    assert report["verification"]["verified"] is True and report["audit_id"] == 3
    assert len(fake.calls) == 1, "the refusal carried its own evidence"
    with pytest.raises(BR.BridgeError) as clearing:
        client.clear_chat(chat=GONE, confirm=True)
    assert clearing.value.code == BR.CODE_CHAT_ALREADY_DELETED


def test_the_destruction_record_is_readable_on_its_own(capsys):
    """The command every one of those refusals tells an operator to run."""
    code, fake = run(["audit", "--title", GONE], v1_audit=audit(DELETED_GONE, audit_row()))
    out = capsys.readouterr().out
    assert code == 0 and [c["method"] for c in fake.calls] == ["GET"], "reading it touches no phone"
    assert "1 destruction(s) recorded" in out, "--title is the chat asked about, not every row"
    assert ("  3  2026-09-21T17:26:16.912Z  delete_chat  '+43 670 4048778'  verified=True  "
            "state='verified'  3 message(s)  chat_list_rescan") in out


# --- the timeout each route gets is the one that route costs ------------------------------------------

def test_a_destructive_call_waits_for_what_a_destruction_costs_the_handset(capsys):
    """The root cause, asserted as a number: the delete that was lost took 91 s and the client gave
    it the generic 90 s. The budget is now derived from the handset's own measured pace (four chat
    list passes at 33 s, the taps' own ceilings, the flock wait) and the read-only routes keep the
    configured floor."""
    code, fake = run(["delete-chat", "--phone", MINE, "--confirm"],
                     v1_chats=CHATS, v1_chats_delete=destroyed("delete_chat"))
    posted = fake.posted(BR.CHAT_DELETE_PATH)
    assert code == 0
    assert posted[0]["timeout"] == 222.0, "30 s flock + 4 x 33 s of chat list + 60 s of taps"
    assert fake.calls[0]["timeout"] == 90.0, "the chat list fits inside WA_BRIDGE_TIMEOUT_SEC"


def test_a_send_keeps_the_budget_it_had(capsys, autosend):
    """The send path was measured and fixed in TASK-146 and is not touched by this: its budget is
    the fixed 90 s plus what the body costs to type."""
    code, fake = run(["send", "--to", MINE, "--body", "x" * 320], v1_messages=sent_echo)
    assert code == 0 and fake.posted(BR.MESSAGES_PATH)[0]["timeout"] == 90 + 320 / 3.2


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


def test_an_unreachable_executor_on_a_destruction_says_check_before_pressing_again(capsys):
    """The tunnel died with the delete in flight and the audit cannot be reached either, so both
    outcomes are still open. Saying which one it was would be a guess, and saying nothing is what
    sent an operator round the loop on 2026-09-21."""
    dead = BR.BridgeUnreachable("bridge unreachable: connection reset")
    code, _ = run(["delete-chat", "--phone", MINE, "--confirm"], now=ASKED_AT,
                  v1_chats=CHATS, v1_chats_delete=dead, v1_audit=dead)
    err = capsys.readouterr().err
    assert code == 1, "a lost answer to a delete is not a verdict"
    assert ("ERROR: the bridge answer was lost (bridge unreachable: connection reset) and the "
            "audit could not be read either (bridge unreachable: connection reset), so whether "
            "'Ivan Test' was destroyed is NOT KNOWN from here. Check with: tools/wa_bridge.py "
            "audit --title 'Ivan Test'") in err


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


# --- the archive flag is the operator's assertion, not a field read off the row -----------------------

ARCHIVED_CHAT = (200, {"ok": True, "count": 1, "chats": [
    {"title": "Alte Bewerberin", "phone": "+4915550000009", "phone_source": "address_book",
     "ambiguous": False, "unread": 0, "stamp": "18.08.26", "archived": True, "has_preview": True}]})


def test_an_archived_chat_is_not_destroyed_by_a_command_that_never_said_archived(capsys):
    """Seven archived threads are on that handset and nobody authorised touching them. Filling the
    flag in from the row we just listed is the CLI asserting it on the operator's behalf, and the
    executor's archive gate then passes itself."""
    code, fake = run(["delete-chat", "--title", "Alte Bewerberin", "--confirm"],
                     v1_chats=ARCHIVED_CHAT, v1_chats_delete=destroyed("delete_chat"))
    assert code == 2 and fake.posted(BR.CHAT_DELETE_PATH) == [], "nothing was touched"
    assert "the archive" in capsys.readouterr().err


def test_a_main_list_chat_is_not_destroyed_by_a_command_that_said_archived(capsys):
    """The mirror: --archived is checked against the handset, never dropped."""
    code, fake = run(["delete-chat", "--title", "Ivan Test", "--archived", "--confirm"],
                     v1_chats=CHATS, v1_chats_delete=destroyed("delete_chat"))
    assert code == 2 and fake.posted(BR.CHAT_DELETE_PATH) == []
    assert "main chat list" in capsys.readouterr().err


def test_an_archived_chat_the_operator_named_as_archived_is_destroyed_and_says_so():
    code, fake = run(["delete-chat", "--title", "Alte Bewerberin", "--archived", "--confirm"],
                     v1_chats=ARCHIVED_CHAT, v1_chats_delete=destroyed("delete_chat"))
    posted = fake.posted(BR.CHAT_DELETE_PATH)
    assert code == 0 and posted[0]["body"]["archived"] is True


def test_a_main_list_chat_posts_the_flag_the_operator_did_not_type():
    code, fake = run(["clear-chat", "--title", "Ivan Test", "--confirm"],
                     v1_chats=CHATS, v1_chats_clear=destroyed("clear_chat"))
    assert code == 0 and fake.posted(BR.CHAT_CLEAR_PATH)[0]["body"]["archived"] is False


def test_a_preview_reads_the_chat_on_the_list_the_operator_named(capsys):
    code, fake = run(["delete-chat", "--title", "Alte Bewerberin", "--archived"],
                     v1_chats=ARCHIVED_CHAT, v1_thread=THREAD)
    assert code == 0 and "archived=1" in fake.calls[1]["query"]


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
