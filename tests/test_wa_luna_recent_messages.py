"""The thread's last ten messages in every turn's payload (Ivan, 2026-10-08) and the READ_HISTORY rule that
goes with them. Offline: tmp SQLite, synthetic phone, no model, no network."""
import json
import re

import pytest

from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from app.wa.luna import prompts as P
from app.wa.luna import tools_server as TS

PHONE = "+4915550300001"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    c = ST.db()
    yield c
    c.close()


def _at(n):
    return f"2030-01-01T10:{n // 60:02d}:{n % 60:02d}+00:00"


def _thread(conn, bodies, start=0):
    """Alternating in/out rows, the first one inbound, wamid msg-<n>, one second apart. -> their wamids."""
    wamids = []
    for i, body in enumerate(bodies, start=start):
        wamid = f"msg-{i}"
        (ST.record_inbound if i % 2 == 0 else ST.record_outbound)(conn, PHONE, wamid, body, at=_at(i))
        wamids.append(wamid)
    return wamids


def _recent(conn, turn_key, **kw):
    return LB.turn_context(conn, ST.thread(conn, PHONE), turn_key, **kw)["recent_messages"]


def _texts(recent):
    return [m["text"] for m in recent]


def test_the_tail_is_the_last_ten_messages_oldest_first_both_directions_without_the_latest_inbound(conn):
    _thread(conn, [f"m{i}" for i in range(15)])              # m0 .. m14, even = candidate, odd = us
    ST.record_outbound(conn, PHONE, "after", "written after the turn's inbound", at=_at(40))

    recent = _recent(conn, "msg-14")

    assert _texts(recent) == [f"m{i}" for i in range(4, 14)]  # m4 .. m13: ten, m14 is latest_inbound, "after" is later
    assert [m["direction"] for m in recent] == ["in", "out"] * 5
    assert recent[0] == {"id": recent[0]["id"], "direction": "in", "kind": "text", "text": "m4", "at": _at(4)}
    assert [m["id"] for m in recent] == sorted(m["id"] for m in recent)


def test_a_thread_shorter_than_the_window_gives_what_there_is(conn):
    _thread(conn, ["hello", "hi, region?", "Bayern"])

    assert _texts(_recent(conn, "msg-2")) == ["hello", "hi, region?"]


def test_a_first_contact_has_an_empty_tail(conn):
    _thread(conn, ["hello"])

    assert _recent(conn, "msg-0") == []


def test_a_forgotten_message_is_not_in_the_tail_and_the_window_fills_from_further_back(conn):
    _thread(conn, [f"m{i}" for i in range(15)])
    ST.forget_message(conn, "msg-9")

    assert _texts(_recent(conn, "msg-14")) == [f"m{i}" for i in (3, 4, 5, 6, 7, 8, 10, 11, 12, 13)]


def test_an_outbound_message_meta_reported_undelivered_is_not_in_the_tail(conn):
    _thread(conn, [f"m{i}" for i in range(15)])
    ST.record_message_status(conn, PHONE, {"id": "msg-11", "status": "sent", "timestamp": "1"})
    ST.record_message_status(conn, PHONE, {"id": "msg-11", "status": "failed", "timestamp": "2"})
    ST.record_message_status(conn, PHONE, {"id": "msg-13", "status": "delivered", "timestamp": "3"})

    texts = _texts(_recent(conn, "msg-14"))

    assert "m11" not in texts and "m13" in texts
    assert len(texts) == 10 and texts[0] == "m3"


def test_the_operator_inbox_messages_are_not_in_the_tail(conn):
    _thread(conn, ["hello", "hi"])
    ST.record_outbound(conn, PHONE, "ack", "[agent] taken", meta={"action": "agent_note_ack"}, at=_at(5))
    ST.record_inbound(conn, PHONE, "next", "second question", at=_at(6))

    assert _texts(_recent(conn, "next")) == ["hello", "hi"]


def test_a_message_without_text_reads_as_its_kind_and_a_voice_note_as_its_transcript(conn):
    ST.record_inbound(conn, PHONE, "doc", "", kind="document", at=_at(0))
    ST.record_outbound(conn, PHONE, "tpl", "", kind="template", at=_at(1))
    ST.record_inbound(conn, PHONE, "voice", "ich bin Pflegefachkraft", kind="audio", at=_at(2))
    ST.record_inbound(conn, PHONE, "now", "ok", at=_at(3))

    recent = _recent(conn, "now")

    assert [(m["kind"], m["text"]) for m in recent] == [("document", "[document]"), ("template", "[template]"),
                                                        ("audio", "ich bin Pflegefachkraft")]


def test_rows_the_turn_text_already_carries_are_left_out_of_the_tail(conn):
    _thread(conn, ["a", "x"])
    ST.record_inbound(conn, PHONE, "b", "b", at=_at(5))
    ST.record_inbound(conn, PHONE, "c", "c", at=_at(6))

    assert _texts(_recent(conn, "c", also_in_text=["b"])) == ["a", "x"]
    assert _texts(_recent(conn, "c")) == ["a", "x", "b"]


def test_the_payload_carries_the_tail_and_an_empty_one_without_a_context(conn):
    _thread(conn, ["hello", "hi", "Bayern"])
    ctx = LB.turn_context(conn, ST.thread(conn, PHONE), "msg-2")
    card = {}

    with_ctx = json.loads(LB._user_payload("Bayern", card, LB.requirement_scoreboard(card), {}, context=ctx))
    without = json.loads(LB._user_payload("Bayern", card, LB.requirement_scoreboard(card), {}))

    assert [(m["direction"], m["text"]) for m in with_ctx["recent_messages"]] == [("in", "hello"), ("out", "hi")]
    assert without["recent_messages"] == []


def test_the_tail_and_read_history_cannot_disagree(conn, monkeypatch):
    """Same query, same formatter, same filters: what read_history returns just before the turn's inbound is
    exactly recent_messages."""
    monkeypatch.setenv("WA_LUNA_PHONE", PHONE)
    _thread(conn, [f"m{i}" for i in range(10)])
    ST.record_outbound(conn, PHONE, "ack", "[agent] taken", meta={"action": "agent_note_done"}, at=_at(10))
    _thread(conn, [f"m{i}" for i in range(10, 15)], start=10)
    ST.forget_message(conn, "msg-7")
    ST.record_message_status(conn, PHONE, {"id": "msg-9", "status": "failed", "timestamp": "1"})
    inbound_id = ST.message_by_wamid(conn, "msg-14")["id"]

    recent = _recent(conn, "msg-14")
    paged = TS.read_history(before_id=inbound_id, limit=LB.RECENT_MESSAGES_WINDOW)

    assert paged["messages"] == recent
    assert paged["has_more"] is True and paged["oldest_id"] == recent[0]["id"]


# --- the rule -------------------------------------------------------------------------------------------

def _read_history_rule():
    [rule] = [r for r in P.RULES if r.startswith("READ_HISTORY")]
    return rule


def test_the_read_history_rule_states_the_tail_and_no_longer_discourages_the_tool():
    rule = _read_history_rule()

    assert "recent_messages" in rule
    assert f"last {LB.RECENT_MESSAGES_WINDOW} messages" in rule
    assert "you may call it whenever the answer depends on something that is not in front of you" in rule
    assert not re.search(r"(do not|don't|never)\s+call\s+(any of this|it|read_history)", rule, re.I)
    assert "double-check" not in rule and "call it only" not in rule.lower()
    # what the old rule said besides the discouragement is kept
    for fact in ("has_more", "before_id", "read_document(document_id)", "metadata only", "forgotten",
                 "live phone screen"):
        assert fact in rule


def test_no_other_prompt_line_tells_the_model_to_hold_back_from_read_history():
    for text in [*P.THINK_ORDER, *P.RULES]:
        for sentence in re.split(r"(?<=[.;])\s+", text):
            if "read_history" in sentence:
                assert not re.search(r"\b(do not|don't|never)\s+call\b|\bonly (when|if)\b", sentence, re.I), sentence
