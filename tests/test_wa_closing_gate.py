"""Ivan's closing-bubble invariant (2026-09-24): every reply is an array of bubbles whose LAST bubble
hands the turn back to the candidate.

Two halves, both offline. The first exercises app/wa/luna/closing_gate.py itself with an injected
transport -- no subprocess, no live model, and in particular no test here depends on a real model's
judgement about German text. The second exercises the wiring in app/wa/luna_brain.py::_checked_reply:
what the harness DOES with a rejection, which is where the previous attempt at this (the next_ask
assertion) got it wrong.

Note the autouse ``_closing_gate_offline`` fixture in tests/conftest.py: every other test in this repo
passes the gate for free. The turn()-level tests below override it deliberately.
"""
import json

import pytest

from app.wa import luna_brain as LB
from app.wa.luna import closing_gate as CG
from app.wa.luna import prompts as P

from .test_wa_luna_dialog_rules import _out, fake_client, small  # noqa: F401  (small is a fixture)


def _says(value):
    return lambda payload_text: json.dumps({"closes": value})


def _boom(exc):
    def transport(payload_text):
        raise exc
    return transport


# --- 1. the gate itself -------------------------------------------------------------------------

def test_no_bubbles_is_nothing_to_close_and_never_calls_the_model():
    """The decline path sends a fixed acknowledgement with no model bubbles at all. There is no last
    bubble to judge, so there is nothing to reject -- and no reason to pay for a call."""
    called = []
    v = CG.closes_the_turn([], transport=lambda p: called.append(p) or '{"closes": false}')
    assert (v.closes, v.reason) == (True, "no bubbles")
    v = CG.closes_the_turn(["   ", ""], transport=lambda p: called.append(p) or '{"closes": false}')
    assert (v.closes, v.reason) == (True, "no bubbles")
    assert called == [], "a reply with nothing in it must not reach the classifier"


def test_a_closing_reply_passes_and_a_trailing_off_reply_does_not():
    assert CG.closes_the_turn(["In welcher Region suchen Sie?"], transport=_says(True)) == CG.Verdict(True, "model")
    assert CG.closes_the_turn(["Das ist leider unterschiedlich."], transport=_says(False)) == CG.Verdict(False, "model")


def test_the_payload_carries_the_bubbles_and_nothing_else():
    """No card, no stage, no history, no phone number, no candidate text. Two consequences worth the
    test: nothing the candidate wrote can steer the verdict, and the same reply cannot be acceptable
    in one conversation and not in another -- which is what the removed strictness gradient did."""
    seen = []

    def transport(payload_text):
        seen.append(json.loads(payload_text))
        return '{"closes": true}'

    CG.closes_the_turn(["Erst das.", "Dann die Frage?"], transport=transport)
    assert seen == [{"bubbles": ["Erst das.", "Dann die Frage?"]}]


@pytest.mark.parametrize("transport, reason_starts", [
    (_boom(RuntimeError("closing gate did not answer within 30s")), "transport failed"),
    (_boom(RuntimeError("'claude' is not on PATH")), "transport failed"),
    (lambda p: "I think it closes, yes", "unparseable output"),
    (lambda p: "[true]", "ambiguous verdict"),
    (lambda p: '{"closes": "yes"}', "ambiguous verdict"),
    (lambda p: '{"verdict": true}', "ambiguous verdict"),
])
def test_every_failure_sends_the_reply_unchecked(transport, reason_starts, caplog):
    """The asymmetry, and it is the opposite of agent_note_gate.py's on purpose: this gate stands in
    front of a candidate who is waiting. A dead classifier must cost one weaker German message, never
    a silent rail. Loud in the log, permissive in the outcome."""
    with caplog.at_level("ERROR"):
        v = CG.closes_the_turn(["Irgendwas."], transport=transport)
    assert v.closes is True
    assert v.reason.startswith(reason_starts)
    assert caplog.records, "a gate that fell over silently is a gate nobody will fix"


def test_the_gate_never_raises_on_an_error_it_did_not_anticipate():
    """Called from inside the reply path: an exception escaping here would cost the candidate their
    answer, which is the exact failure this whole module exists to prevent. So the catch is `Exception`,
    not a list of the failures we thought of. (BaseException -- KeyboardInterrupt, SystemExit -- is
    deliberately NOT caught: a shutdown signal is not a classifier failure and must still stop the
    process.)"""
    for exc in (ValueError("weird"), OSError("no fds"), MemoryError()):
        assert CG.closes_the_turn(["x"], transport=_boom(exc)).closes is True


# --- 2. what the harness does with a rejection ---------------------------------------------------

def _gate(monkeypatch, *verdicts):
    """Scripts the gate per call: the Nth composed reply gets the Nth verdict. Patches the transport
    rather than closes_the_turn(), so the real parse/verdict path still runs."""
    calls = []

    def transport(payload_text):
        calls.append(json.loads(payload_text))
        closes = verdicts[len(calls) - 1] if len(calls) <= len(verdicts) else True
        return json.dumps({"closes": closes})

    monkeypatch.setattr(CG, "_live_transport", transport)
    return calls


def test_a_reply_that_trails_off_is_retried_with_a_hint_not_with_the_violation(small, monkeypatch):
    """Ivan: "просто еще одну попытку, но только с хинтом". The model is not handed the harness's
    complaint (that is what a grounding violation gets) -- it is asked for the same turn again, in the
    same session, with one short reminder. What the candidate finally receives is the second attempt."""
    gate_saw = _gate(monkeypatch, False, True)
    payloads = []

    def reply(system, user, session_id):
        payloads.append(user)
        if len(payloads) == 1:
            return _out(bubbles=["Das ist leider ganz unterschiedlich."]), session_id
        return _out(bubbles=["Das ist unterschiedlich.", "In welcher Region suchen Sie?"]), session_id

    d = LB.turn("Wo ist die Klinik?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))

    assert d["bubbles"] == ["Das ist unterschiedlich.", "In welcher Region suchen Sie?"]
    assert d["action"] == "reply_after_correction"
    assert len(payloads) == 2, "exactly one more attempt, not a loop"
    hint = json.loads(payloads[1])
    assert set(hint) == {"instruction"}, "a hint only -- no violation text quoted back"
    assert "harness_rejected_your_reply" not in payloads[1]
    assert LB.CLOSING_HINT == hint["instruction"]
    assert [c["bubbles"] for c in gate_saw] == [
        ["Das ist leider ganz unterschiedlich."],
        ["Das ist unterschiedlich.", "In welcher Region suchen Sie?"]]


def test_a_second_reply_that_still_trails_off_is_sent_anyway_and_nobody_is_called(small, monkeypatch):
    """Ivan, explicit: "нет такого, что мы каждое сообщение проверяем на гейт, а потом зовем
    человека". A weak answer is still an answer to what the candidate asked; it is not a false one.
    So: no holding message, no escalation on the card -- one ERROR line and the reply goes."""
    _gate(monkeypatch, False, False)

    def reply(system, user, session_id):
        return _out(bubbles=["Das ist leider ganz unterschiedlich."]), session_id

    d = LB.turn("Wo ist die Klinik?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))

    assert d["bubbles"] == ["Das ist leider ganz unterschiedlich."]
    assert d["bubbles"] != [P.BLOCKED_REPLY_DE], "the holding message is for a false reply, not a weak one"
    assert not d["slots"].get("_escalated")
    assert not d["slots"].get("_escalate_reason")


def test_a_grounding_violation_still_escalates_after_two_strikes(small, monkeypatch):
    """The regression guard for the change above: relaxing the CLOSING ending must not relax the
    grounding ending. An invented link twice running still buys a holding message and a human."""
    _gate(monkeypatch, True, True)

    def reply(system, user, session_id):
        return _out(bubbles=["Hier die Übersicht: https://pflege-job-radar.de/jobs"]), session_id

    d = LB.turn("schicken Sie mir mal was", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))

    assert d["bubbles"] == [P.BLOCKED_REPLY_DE]
    assert "LINK" in d["slots"]["_escalate_reason"] and d["slots"]["_escalated"] is True


def test_a_closing_reply_is_sent_on_the_first_pass_with_no_second_call(small, monkeypatch):
    """The ordinary case has to stay one model call: this gate runs on every candidate turn, and a
    reply that already closes must not pay for a retry."""
    _gate(monkeypatch, True)
    payloads = []

    def reply(system, user, session_id):
        payloads.append(user)
        return _out(bubbles=["Gern!", "In welcher Region suchen Sie?"]), session_id

    d = LB.turn("Hallo", {"slots": {"region": "Bayern"}, "asked": []}, client=fake_client(reply))
    assert d["bubbles"] == ["Gern!", "In welcher Region suchen Sie?"]
    assert d["action"] != "reply_after_correction"
    assert len(payloads) == 1


def test_the_gate_judges_the_text_not_the_models_own_next_ask(small, monkeypatch):
    """Why this replaced the next_ask assertion. Here the model SAYS it asked something -- next_ask is
    filled in, exactly as the old check required -- while the bubble it actually wrote asks nothing.
    The old check passed this turn; this one does not."""
    _gate(monkeypatch, False, True)
    seen = []

    def reply(system, user, session_id):
        seen.append(user)
        if len(seen) == 1:
            return _out(bubbles=["Ich schaue mal nach."],
                        next_ask="In welcher Region suchen Sie?"), session_id
        return _out(bubbles=["Ich schaue nach.", "In welcher Region suchen Sie?"]), session_id

    d = LB.turn("Haben Sie was?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"][-1] == "In welcher Region suchen Sie?"
    assert len(seen) == 2, "a filled-in next_ask must not rescue a bubble that asks nothing"


def test_the_same_reply_is_judged_identically_however_far_along_the_candidate_is(small, monkeypatch):
    """A strictness gradient keyed on the checklist was built and removed the same day (Ivan agreed):
    "one gate left" is by construction the CONSENT stage, so softening near the end switched leniency
    on at exactly the ask that produces the outcome. The gate now sees the bubbles and nothing else,
    so an empty card and an almost-complete one send byte-identical payloads."""
    seen = _gate(monkeypatch, True, True)

    def reply(system, user, session_id):
        return _out(bubbles=["Alles klar.", "In welcher Region suchen Sie?"]), session_id

    LB.turn("Passt", {"slots": {}, "asked": []}, client=fake_client(reply))
    LB.turn("Passt", {"slots": {"region": "Bayern", "qualification_path": "urkunde",
                                "city": "München", "housing_needed": False}, "asked": []},
            client=fake_client(reply))

    assert len(seen) == 2
    assert seen[0] == seen[1], "the gate must not be told where in the funnel the candidate is"
    assert set(seen[0]) == {"bubbles"}


# --- 3. the retry's own failures (workflow findings, 2026-09-24, each confirmed by probe) --------

@pytest.mark.parametrize("boom, what", [
    (RuntimeError("claude -p did not answer within 552s"), "the retry timed out"),
    (LB.SessionNotFound("session gone"), "the session vanished between the two calls"),
    (RuntimeError("reply has no card_patch"), "_validate rejected the retry's shape"),
])
def test_a_retry_that_fails_for_a_non_rule_reason_still_answers_the_candidate(small, monkeypatch,
                                                                             boom, what):
    """The critical one. The retry is a whole second model call and fails in ways that are not rule
    violations at all. Those used to escape turn() and the candidate got NOTHING, while a truthful,
    already-checked reply sat in hand. A violation costs the model its draft, never the candidate
    their answer."""
    _gate(monkeypatch, False)
    calls = []

    def reply(system, user, session_id):
        calls.append(user)
        if len(calls) == 1:
            return _out(bubbles=["Das ist leider ganz unterschiedlich."]), session_id
        raise boom

    d = LB.turn("Wo ist die Klinik?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"] == ["Das ist leider ganz unterschiedlich."], what
    assert not d["slots"].get("_escalated")


@pytest.mark.parametrize("retry_bubbles", [[], ["Eins.", "Zwei.", "Drei."]])
def test_a_closing_failure_whose_rewrite_breaks_the_style_rule_is_not_a_grounding_escalation(
        small, monkeypatch, retry_bubbles):
    """The hint says "write the SAME turn again", and a model reading that reasonably comes back with
    nothing further to add, or with three bubbles the prompt elsewhere licenses. Keying the escape
    hatch on the SECOND failure filed those as `grounding_rule_violated_twice` and paged a colleague
    over a rule nobody broke."""
    _gate(monkeypatch, False)
    calls = []

    def reply(system, user, session_id):
        calls.append(user)
        if len(calls) == 1:
            return _out(bubbles=["Das ist leider ganz unterschiedlich."]), session_id
        return _out(bubbles=retry_bubbles), session_id

    d = LB.turn("Wo ist die Klinik?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"] == ["Das ist leider ganz unterschiedlich."]
    assert d["bubbles"] != [P.BLOCKED_REPLY_DE]
    assert not d["slots"].get("_escalated")


def test_a_closing_failure_whose_rewrite_invents_a_link_sends_the_checked_first_reply(small,
                                                                                     monkeypatch):
    """The rewrite is discarded on its own merits -- it broke a grounding rule -- but the first reply
    already passed every check, so it goes. Nobody is called: nothing false was ever composed twice."""
    _gate(monkeypatch, False)
    calls = []

    def reply(system, user, session_id):
        calls.append(user)
        if len(calls) == 1:
            return _out(bubbles=["Das ist leider ganz unterschiedlich."]), session_id
        return _out(bubbles=["Hier die Übersicht: https://pflege-job-radar.de/jobs"]), session_id

    d = LB.turn("Wo ist die Klinik?", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"] == ["Das ist leider ganz unterschiedlich."]
    assert not d["slots"].get("_escalated")


def test_a_grounding_failure_whose_retry_times_out_still_calls_a_human(small, monkeypatch):
    """The other side of keying on the FIRST failure, and the reason it is not simply "always send
    something": a reply rejected for inventing a link cannot be sent whatever happens next."""
    _gate(monkeypatch, True)
    calls = []

    def reply(system, user, session_id):
        calls.append(user)
        if len(calls) == 1:
            return _out(bubbles=["Hier die Übersicht: https://pflege-job-radar.de/jobs"]), session_id
        raise RuntimeError("claude -p did not answer within 552s")

    d = LB.turn("schicken Sie mir mal was", {"slots": {"region": "Bayern"}, "asked": []},
                client=fake_client(reply))
    assert d["bubbles"] == [P.BLOCKED_REPLY_DE]
    assert d["slots"]["_escalated"] is True
