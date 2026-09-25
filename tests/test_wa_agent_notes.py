"""The operator inbox (Ivan, 2026-09-24): a Russian instruction that arrives on a TEST thread is
recorded for the worker and acknowledged, never handed to the candidate brain.

The live incident this is built from (2026-09-24 17:26 UTC): a Russian voice note asking to re-send a
broadcast was answered by Luna in German four times -- it introduced itself as Valentina, apologised
about the voice note, and asked whether he was looking for work in Bayern -- while the instruction
itself reached nobody who could act on it.

Everything here is offline: tmp SQLite, a fake Meta client, a fake model, and the language gate's own
``transport=`` seam, so nothing spawns the real `claude` CLI or talks to Meta or the board.
"""
import json
import time

import pytest

from app import data as D
from app.wa import api as WAPI
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa import store as ST
from app.wa.luna import agent_note_gate as GATE
from app.wa.luna import agent_notes as AN
from app.wa.luna import reporting as REP
from app.wa.luna import shadow_run as SR

PHONE_ID = "555000111"
OPERATOR = "+4915550200001"        # marked is_test: Ivan's / Valentyn's own number
CANDIDATE = "+4915550200002"       # a real lead, never touched by this feature

RU_NOTE = "Отправь мне рассылку еще раз, хочу проверить, что на новой версии кода все работает."
DE_TEST_TURN = "Guten Tag, ich suche eine Stelle als Pflegefachkraft in Bayern."


@pytest.fixture()
def wa(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    monkeypatch.setattr(C, "PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setattr(C, "ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(C, "AUTOSEND", True)
    monkeypatch.setattr(C, "BRAIN", "luna")
    monkeypatch.setattr(C, "LUNA_MAX_CALLS_PER_HOUR", 0)
    with ST.db() as c:
        for phone, is_test in ((OPERATOR, 1), (CANDIDATE, 0)):
            t = ST.thread(c, phone)
            ST.save_thread(c, t)
            if is_test:
                ST.mark_test_thread(c, phone, True)
    return tmp_path


class FakeMeta:
    """Models the rail this actually runs on: the phone bridge, whose consumer chat has no 24h
    free-form window (app/wa/bridge.py: ``requires_freeform_window = False``). A completion note that
    goes out hours after its ack is therefore ordinary free text here -- see
    test_a_completion_note_obeys_the_rail_s_own_window_rule for what a Meta-rail thread does instead."""

    requires_freeform_window = False

    def __init__(self):
        self.sent = []

    def send_text(self, to_e164, body):
        self.sent.append((to_e164, body))
        return f"wamid.out.{len(self.sent)}"

    def send_buttons(self, to_e164, body, buttons):
        return self.send_text(to_e164, body)


def _gate(monkeypatch, verdict):
    """The language gate answers ``verdict`` without ever reaching the real CLI."""
    monkeypatch.setattr(GATE, "is_operator_note",
                        lambda text, transport=None: GATE.Verdict(verdict, "model"))


def _brain(monkeypatch, calls):
    """Records every call the candidate brain gets, so a test can assert it got none."""
    def turn(text, t, button_id=None):
        calls.append(text)
        return {"action": "reply_now_conversational", "bubbles": ["Guten Tag!"], "buttons": [],
                "slots": t.get("slots", {}), "asked": t.get("asked", []), "stopped": False,
                "matches": [], "next_ask": "Wo suchen Sie?"}
    monkeypatch.setattr(LB, "turn", turn)


def _deliver(phone, wamid, text, client):
    body = {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages",
            "value": {"messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
                      "messages": [{"id": wamid, "from": phone[1:], "type": "text",
                                    "text": {"body": text}}]}}]}]}
    return WAPI.handle_payload(body, client=client)["results"]


# --- the gate itself ----------------------------------------------------------------------------

def test_cyrillic_is_a_codepoint_fact_decided_without_a_model_call():
    """Stage 0 is code because it is a fact, not a judgement -- and it is what keeps a German test
    turn from ever paying for a classifier call."""
    assert GATE.has_cyrillic(RU_NOTE)
    assert not GATE.has_cyrillic(DE_TEST_TURN)
    assert not GATE.has_cyrillic("")
    assert GATE.is_operator_note(DE_TEST_TURN) == GATE.Verdict(False, "no cyrillic")


@pytest.mark.parametrize("transport, reason", [
    (lambda payload: (_ for _ in ()).throw(RuntimeError("claude is not on PATH")), "transport failed"),
    (lambda payload: "not json at all", "unparseable output"),
    (lambda payload: '["a list"]', "ambiguous verdict"),
    (lambda payload: '{"operator_note": "yes"}', "ambiguous verdict"),
])
def test_every_classifier_failure_resolves_to_an_ordinary_candidate_turn(transport, reason):
    """The asymmetry: a false positive breaks the test run the operator is in the middle of and the
    reply they waited for never comes; a false negative costs one German non-sequitur to the person
    best equipped to notice it. So every failure mode points the same way."""
    verdict = GATE.is_operator_note(RU_NOTE, transport=transport)
    assert verdict.is_note is False and reason in verdict.reason


def test_a_clean_boolean_is_taken_as_the_verdict():
    assert GATE.is_operator_note(RU_NOTE, transport=lambda p: '{"operator_note": true}').is_note is True
    assert GATE.is_operator_note(RU_NOTE, transport=lambda p: '{"operator_note": false}').is_note is False


def test_the_gate_sends_only_the_message_and_no_thread_history():
    """A classifier that cannot be told anything but the one message is a classifier nothing
    downstream can be talked into -- it runs --restricted --tools "" and answers one boolean."""
    seen = []

    def transport(payload_text):
        seen.append(json.loads(payload_text))
        return '{"operator_note": true}'

    GATE.is_operator_note(RU_NOTE, transport=transport)
    assert seen == [{"message": RU_NOTE}]


# --- routing, end to end ------------------------------------------------------------------------

def test_a_russian_note_on_a_test_thread_is_recorded_and_acked_and_never_reaches_the_brain(wa, monkeypatch):
    """The live incident, inverted: the instruction lands on its own row, the sender gets one ack,
    and Luna is never asked to answer it."""
    _gate(monkeypatch, True)
    calls = []
    _brain(monkeypatch, calls)
    meta = FakeMeta()

    (result,) = _deliver(OPERATOR, "wamid.note1", RU_NOTE, meta)

    assert result["status"] == ST.AGENT_NOTE_STATE and result["action"] == "agent_note_ack"
    assert calls == [], "the candidate brain must never see an operator's instruction"
    with ST.db() as c:
        row = ST.agent_note_for_wamid(c, "wamid.note1")
        assert row["phone"] == OPERATOR and row["body"] == RU_NOTE and row["status"] == "pending"
        assert row["acked_at"] and row["attempts"] == 0
        assert ST.reply_turn_claim_state(c, OPERATOR, "wamid.note1") == ST.AGENT_NOTE_STATE
    assert len(meta.sent) == 1
    to, body = meta.sent[0]
    assert to == OPERATOR and body.startswith("[агент] Принято, заметка #")


def test_a_german_test_turn_on_the_same_thread_is_answered_by_the_brain_as_always(wa, monkeypatch):
    """An operator role-playing a candidate is the other half of what a test thread is for: it must
    keep working exactly as before, and without paying for a classifier call."""
    def boom(text, transport=None):
        raise AssertionError("a message with no Cyrillic must never reach the classifier")
    monkeypatch.setattr(GATE, "is_operator_note", boom)
    calls = []
    _brain(monkeypatch, calls)

    (result,) = _deliver(OPERATOR, "wamid.de1", DE_TEST_TURN, FakeMeta())

    assert calls == [DE_TEST_TURN]
    assert result["status"] != ST.AGENT_NOTE_STATE
    with ST.db() as c:
        assert ST.agent_note_for_wamid(c, "wamid.de1") is None


def test_the_feature_does_not_exist_for_a_real_candidate(wa, monkeypatch):
    """Ivan's own framing: for a non-test user there is no gate at all. A Russian-speaking nurse --
    and this rail recruits many -- is answered as the candidate she is, whatever she writes."""
    def boom(text, transport=None):
        raise AssertionError("the gate must never run on a thread that is not marked is_test")
    monkeypatch.setattr(GATE, "is_operator_note", boom)
    calls = []
    _brain(monkeypatch, calls)

    (result,) = _deliver(CANDIDATE, "wamid.cand1", "Здравствуйте, ваш бот прислал мне одно и то же три раза",
                         FakeMeta())

    assert calls and result["status"] != ST.AGENT_NOTE_STATE
    with ST.db() as c:
        assert ST.agent_note_for_wamid(c, "wamid.cand1") is None


def test_a_redelivered_note_is_not_recorded_or_acked_twice(wa, monkeypatch):
    """Meta redelivers, catch-up re-drives, a worker crashes: the same wamid must produce one row and
    one ack. The row is looked up before anything else, so the second pass does not even classify."""
    calls = []
    _brain(monkeypatch, calls)
    gate_calls = []

    def gate(text, transport=None):
        gate_calls.append(text)
        return GATE.Verdict(True, "model")
    monkeypatch.setattr(GATE, "is_operator_note", gate)
    meta = FakeMeta()

    _deliver(OPERATOR, "wamid.note2", RU_NOTE, meta)
    with ST.db() as c:      # the claim is terminal, so a re-drive gets nowhere near the brain
        assert ST.claim_reply_turn(c, OPERATOR, "wamid.note2") is False
        t = ST.thread(c, OPERATOR)
        again = WAPI._route_agent_note(c, t, {"wamid": "wamid.note2", "kind": "text"}, RU_NOTE, client=meta)

    assert again["status"] == "claimed_elsewhere"
    assert len(gate_calls) == 1, "the second pass finds the row and never calls the classifier again"
    assert len(meta.sent) == 1, "one ack, not two"
    with ST.db() as c:
        rows = c.execute("select count(*) as n from wa_agent_notes where wamid=?", ("wamid.note2",)).fetchone()
        assert rows["n"] == 1


def test_a_routed_note_leaves_the_thread_answered_not_owed(wa, monkeypatch):
    """Routing must not leave a reply owed. In the ordinary case the ack itself is the last message,
    so the thread reads "them" -- answered -- and catch-up has nothing to re-drive."""
    _gate(monkeypatch, True)
    _brain(monkeypatch, [])
    _deliver(OPERATOR, "wamid.note3", RU_NOTE, FakeMeta())

    with ST.db() as c:
        assert REP.ball_for(c, OPERATOR) == "them"
        assert OPERATOR not in SR.phones_owed_a_reply(c, include_test=True)


def test_the_routed_claim_state_counts_as_settled_even_with_no_outbound_behind_it(wa):
    """The ack normally sits behind the note, so the checks above never see the inbound as last. They
    do once that ack is soft-deleted (TASK-289 "forget" hides the row from every read) -- and then the
    claim state is the only thing left saying this message was answered. Without AGENT_NOTE_STATE in
    these two, catch-up would hand a Russian instruction to the candidate brain, which is the exact
    failure wa_agent_notes exists to prevent."""
    with ST.db() as c:
        ST.record_inbound(c, OPERATOR, "wamid.note4", RU_NOTE)
        ST.claim_reply_turn(c, OPERATOR, "wamid.note4")
        ST.finish_reply_turn_claim(c, OPERATOR, "wamid.note4", ST.AGENT_NOTE_STATE)

        assert REP.ball_for(c, OPERATOR) == "silent"
        assert OPERATOR not in SR.phones_owed_a_reply(c, include_test=True)


# --- the worker's half --------------------------------------------------------------------------

def _note(c, wamid="wamid.w1"):
    return ST.record_agent_note(c, wamid, OPERATOR, RU_NOTE, "text")


def test_a_note_is_claimed_by_exactly_one_worker(wa):
    with ST.db() as c:
        row = _note(c)
        assert ST.claim_agent_note(c, row["id"]) is True
        assert ST.claim_agent_note(c, row["id"]) is False, "a live claim is not takeable"
        assert ST.agent_note(c, row["id"])["attempts"] == 1


def test_a_worker_that_died_releases_its_note_after_the_stale_window(wa, monkeypatch):
    with ST.db() as c:
        row = _note(c)
        ST.claim_agent_note(c, row["id"])
        assert [r["id"] for r in ST.open_agent_notes(c)] == [], "a fresh claim is not re-offered"
        # Negative, not 0: now_iso() drops microseconds, so a 0-second window puts the cutoff on the
        # same whole second as claimed_at and the strict < would not fire.
        monkeypatch.setattr(ST, "AGENT_NOTE_STALE_SEC", -5)
        assert [r["id"] for r in ST.open_agent_notes(c)] == [row["id"]]
        assert ST.claim_agent_note(c, row["id"]) is True
        assert ST.agent_note(c, row["id"])["attempts"] == 2


def test_progress_survives_across_workers(wa):
    """A tick that cannot finish in one pass leaves the next one a trail instead of starting over."""
    with ST.db() as c:
        row = _note(c)
        ST.append_agent_note_progress(c, row["id"], "read the thread")
        ST.append_agent_note_progress(c, row["id"], "wrote the fix")
        trail = ST.agent_note(c, row["id"])["progress"].splitlines()
    assert len(trail) == 2 and trail[0].endswith("read the thread") and trail[1].endswith("wrote the fix")


def test_the_completion_note_is_one_fixed_format_with_every_line_present(wa):
    note = AN.completion_note(7, AN.DONE_WORD, "Gate gebaut", "", "nichts")
    assert note.splitlines() == ["[агент] Заметка #7 — готово", "Сделано: Gate gebaut",
                                 f"Не сделано: {AN.EMPTY}", "Нужно: nichts"]


def test_a_long_field_is_cut_rather_than_dropped(wa):
    note = AN.completion_note(7, AN.DONE_WORD, "x" * 500, "y", "z")
    done = [line for line in note.splitlines() if line.startswith("Сделано:")][0]
    assert done.endswith("…") and len(done) <= AN.FIELD_MAX + len("Сделано: ")
    assert "Не сделано: y" in note and "Нужно: z" in note


def test_finishing_a_note_sends_exactly_one_completion_message(wa, monkeypatch):
    meta = FakeMeta()
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: meta)
    with ST.db() as c:
        row = _note(c)
        assert AN._send_completion(c, row["id"], AN.DONE_WORD, "fertig", "", "") == "sent"
        assert AN._send_completion(c, row["id"], AN.DONE_WORD, "fertig", "", "") == "already_notified"
    assert len(meta.sent) == 1 and meta.sent[0][1].startswith(f"[агент] Заметка #{row['id']} — готово")


def test_a_failed_completion_send_stays_retryable(wa, monkeypatch):
    """mark-then-send would burn the single delivery on a send that never happened."""
    class Failing(FakeMeta):
        def send_text(self, to_e164, body):
            raise RuntimeError("bridge unreachable")
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: Failing())
    with ST.db() as c:
        row = _note(c)
        with pytest.raises(RuntimeError):
            AN._send_completion(c, row["id"], AN.DONE_WORD, "fertig", "", "")
        assert ST.agent_note(c, row["id"])["notified_at"] is None


def test_a_completion_note_obeys_the_rail_s_own_window_rule(wa, monkeypatch):
    """Recorded, not worked around: on a rail that DOES have Meta's 24h free-form window, a completion
    note finished long after the note arrived is not deliverable as free text, and _send's existing
    rule applies to it like to any other send. The live rail is the bridge, which has no such window
    (see FakeMeta), so this is a property of the Meta rail only -- worth pinning so a future rail
    switch does not turn completion notes into silent reopen templates unnoticed."""
    class MetaStyle(FakeMeta):
        requires_freeform_window = True
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: MetaStyle())
    with ST.db() as c:
        row = _note(c, wamid="wamid.window")
        with pytest.raises(RuntimeError, match="free-form window closed"):
            AN._send_completion(c, row["id"], AN.DONE_WORD, "fertig", "", "")
        assert ST.agent_note(c, row["id"])["notified_at"] is None, "still retryable"


def test_neither_ack_nor_completion_reuses_the_inbound_wamid_as_its_turn_key(wa, monkeypatch):
    """bridge_ids.reply_key hashes phone|turn_key|bubble_index and leaves action out on purpose, so
    the inbound wamid would mint the same client_msg_id as bubble 0 of the candidate turn for that
    same message. Reachable: a gate timeout answers the message as an ordinary turn, and a catch-up
    re-drive that then classifies it as a note hits first-body-wins and wedges the message forever --
    three such rows exist on the live rail. Both of the inbox's own sends need their own key."""
    keys = []

    class KeyedMeta(FakeMeta):
        wants_idempotency_key = True

        def begin_turn(self, phone, turn_key, action):
            keys.append(turn_key)
    meta = KeyedMeta()
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: meta)
    monkeypatch.setattr(WAPI.T, "rail_of_client", lambda cl: "bridge")
    _gate(monkeypatch, True)
    _brain(monkeypatch, [])

    _deliver(OPERATOR, "wamid.keyed", RU_NOTE, meta)
    with ST.db() as c:
        note_id = ST.agent_note_for_wamid(c, "wamid.keyed")["id"]
        AN._send_completion(c, note_id, AN.DONE_WORD, "fertig", "", "")

    assert keys == [f"agent_note:{note_id}:ack", f"agent_note:{note_id}:done"]
    assert "wamid.keyed" not in keys


def test_a_completion_that_was_not_actually_sent_raises_and_stays_retryable(wa, monkeypatch):
    """The failure this actually hit: the worker runs the CLI from a plain shell, where none of the
    unit's EnvironmentFile= ever loaded, so WA_AUTOSEND is unset and _send records a draft and returns
    "draft". Taking that as success would spend the note's one completion on a message nobody
    received, and mark_agent_note_notified would refuse forever after."""
    monkeypatch.setattr(C, "AUTOSEND", False)
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: FakeMeta())
    with ST.db() as c:
        row = _note(c, wamid="wamid.draft")
        with pytest.raises(RuntimeError, match="was not sent"):
            AN._send_completion(c, row["id"], AN.DONE_WORD, "fertig", "", "")
        assert ST.agent_note(c, row["id"])["notified_at"] is None
        # and it comes back as takeable, or nothing would ever look at it again
        ST.finish_agent_note(c, row["id"], "done", "fertig", "", "")
        assert row["id"] in [r["id"] for r in ST.open_agent_notes(c)]


def test_the_inbox_s_own_messages_are_invisible_to_the_candidate_brain(wa, monkeypatch):
    """Ivan's own convention is Russian-to-the-devs and German-to-test on the SAME thread, so the ack
    sits directly between the operator's test turns. If it counted as "what we last told this
    candidate", the next German turn would be read as answering "[агент] Принято, заметка #1" and
    handed to the refusal classifier as our_last_message."""
    _gate(monkeypatch, True)
    _brain(monkeypatch, [])
    _deliver(OPERATOR, "wamid.note9", RU_NOTE, FakeMeta())

    with ST.db() as c:
        ST.record_inbound(c, OPERATOR, "wamid.de9", DE_TEST_TURN)
        t = ST.thread(c, OPERATOR)
        ctx = LB.turn_context(c, t, "wamid.de9")
        bodies = [v["body"] for v in ctx["outbound_since_last_turn"]]
        page, _ = ST.messages_before(c, OPERATOR)

    assert not any("[агент]" in b for b in bodies)
    assert ctx["last_outbound"] is None or "[агент]" not in ctx["last_outbound"]["body"]
    assert not any("[агент]" in (r["body"] or "") for r in page), "nor when the model pages history"


def test_a_voice_note_placeholder_is_not_answered_while_its_transcript_is_still_coming(wa, monkeypatch):
    """A voice note is TWO inbound rows ~20s apart: the rail's placeholder ("🎤 Sprachnachricht
    (0:21)", no Cyrillic, so the gate cannot recognise it) and then the transcript. Live, 2026-09-24:
    the placeholder was answered with "Ihre Sprachnachricht ist hier leider nicht abspielbar
    angekommen" four seconds BEFORE its own transcript existed."""
    calls = []
    _brain(monkeypatch, calls)
    meta = FakeMeta()

    with ST.db() as c:
        ST.record_inbound(c, OPERATOR, "wamid.ph", "🎤 Sprachnachricht (0:21)",
                          meta={"media_pending": "audio"})
        t = ST.thread(c, OPERATOR)
        result = WAPI.finish_inbound(c, WAPI.message_from_row(
            c.execute("select * from wa_messages where wamid=?", ("wamid.ph",)).fetchone()), client=meta)

    assert result["status"] == "media_pending_placeholder"
    assert calls == [] and meta.sent == [], "no candidate turn, and nothing sent to the operator"


def test_a_real_candidate_s_voice_note_placeholder_keeps_todays_behaviour(wa, monkeypatch):
    """The suppression above is scoped to test threads on purpose: a real candidate's voice note must
    not change behaviour as a side effect of an operator feature."""
    calls = []
    _brain(monkeypatch, calls)

    with ST.db() as c:
        ST.record_inbound(c, CANDIDATE, "wamid.ph2", "🎤 Sprachnachricht (0:12)",
                          meta={"media_pending": "audio"})
        result = WAPI.finish_inbound(c, WAPI.message_from_row(
            c.execute("select * from wa_messages where wamid=?", ("wamid.ph2",)).fetchone()),
            client=FakeMeta())

    assert result["status"] != "media_pending_placeholder" and calls == ["🎤 Sprachnachricht (0:12)"]


def test_the_operator_thread_is_not_nudged_by_the_automatic_sweep(wa, monkeypatch):
    """A thread that goes quiet because a note is being worked on is not a candidate going quiet --
    and _note_arrival moves last_inbound_at, so every note would start a fresh nudge streak."""
    from app.wa.luna import followups as FU
    _gate(monkeypatch, True)
    _brain(monkeypatch, [])
    _deliver(OPERATOR, "wamid.note10", RU_NOTE, FakeMeta())
    monkeypatch.setattr(FU, "_in_quiet_hours", lambda: False)

    assert [r for r in FU.run(client=FakeMeta()) if r.get("phone") == OPERATOR] == []


def test_the_completion_note_does_not_reuse_the_ack_s_turn_key(wa, monkeypatch):
    """app/wa/bridge_ids.reply_key hashes phone|turn_key|bubble_index and leaves action out on
    purpose, so reusing the inbound wamid here would mint the ack's own client_msg_id -- the
    executor's ledger would replay it and the completion note would never be delivered while the row
    read 'done'."""
    keys = []

    class KeyedMeta(FakeMeta):
        wants_idempotency_key = True
        requires_freeform_window = False

        def begin_turn(self, phone, turn_key, action):
            keys.append(turn_key)
    monkeypatch.setattr(WAPI.T, "get_client", lambda phone=None, client=None, conn=None: KeyedMeta())
    monkeypatch.setattr(WAPI.T, "rail_of_client", lambda cl: "bridge")
    with ST.db() as c:
        row = _note(c, wamid="wamid.keyed")
        AN._send_completion(c, row["id"], AN.DONE_WORD, "fertig", "", "")
    assert keys == [f"agent_note:{row['id']}:done"] and "wamid.keyed" not in keys
