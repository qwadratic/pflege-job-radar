"""TASK-457: the Jev decision client (app/wa/luna/jev.py) and the two gate transports that now
ride on it (closing_gate._live_transport, refusal._live_transport).

Offline like the rest of this suite: decide() takes an injected transport (the HTTP seam, the
same _default_transport pattern as app/wa/stt.py), and the gate tests monkeypatch the shared
jev module's decide -- no subprocess, no network, and no test here depends on a live model's
judgement. The live before/after numbers live in evals/jev_gates/bench.py, which is a script,
not a test.
"""
import json

import pytest

from app.wa import config as C
from app.wa.luna import closing_gate as CG
from app.wa.luna import jev
from app.wa.luna import refusal as R

# The autouse _closing_gate_offline fixture in tests/conftest.py replaces
# closing_gate._live_transport for every test (no offline test may spawn a model call). Capture
# the real one at import time -- before any fixture runs -- so the tests below that want to
# exercise the Jev transport itself can restore it (a later monkeypatch.setattr in the test body
# wins over the fixture's, per the fixture's own docstring).
_REAL_CLOSING_TRANSPORT = CG._live_transport


# --- jev.decide: the client contract --------------------------------------------------------

def test_decide_posts_state_and_questions_and_returns_the_decision(monkeypatch):
    monkeypatch.setattr(C, "OPENROUTER_API_KEY", "test-key")
    seen = {}

    def transport(url, payload, key, timeout):
        seen.update(url=url, payload=payload, key=key, timeout=timeout)
        return {"decision": {"is_refusal": 0.9}, "usage": {"input_tokens": 10}}

    d = jev.decide(
        {"candidate_reply": "nein, danke"},
        {"is_refusal": {"type": "noul", "threshold": 0.5, "question": "q?"}},
        transport=transport,
    )
    assert d == {"is_refusal": 0.9}
    assert seen["url"] == jev.DECISIONS_URL
    assert seen["payload"]["state"] == {"candidate_reply": "nein, danke"}
    assert seen["payload"]["questions"]["is_refusal"]["question"] == "q?"
    assert seen["payload"]["model"] == C.JEV_MODEL
    assert seen["timeout"] == C.JEV_TIMEOUT_SEC


def test_decide_without_a_key_fails_loud_before_any_call(monkeypatch):
    monkeypatch.setattr(C, "OPENROUTER_API_KEY", "")
    called = []
    with pytest.raises(jev.DecisionError, match="WA_OPENROUTER_API_KEY"):
        jev.decide({}, {"q": {"type": "noul", "question": "q?"}},
                   transport=lambda *a, **k: called.append(1) or {})
    assert called == []


@pytest.mark.parametrize("body", [
    {"error": "nope"},       # no decision at all
    {"decision": {}},        # empty decision
    "not-an-object",         # body is not a JSON object
])
def test_decide_rejects_bodies_without_a_usable_decision(monkeypatch, body):
    monkeypatch.setattr(C, "OPENROUTER_API_KEY", "test-key")
    with pytest.raises(jev.DecisionError):
        jev.decide({}, {"q": {"type": "noul", "question": "q?"}},
                   transport=lambda *a, **k: body)


def test_decide_wraps_transport_failures_in_decision_error(monkeypatch):
    monkeypatch.setattr(C, "OPENROUTER_API_KEY", "test-key")

    def boom(*a, **k):
        raise OSError("connection reset")

    with pytest.raises(jev.DecisionError, match="connection reset"):
        jev.decide({}, {"q": {"type": "noul", "question": "q?"}}, transport=boom)


# --- the gate transports: Jev probability -> the gates' existing contract -------------------

def _fake_decide(value):
    def decide(state, questions, **kw):
        return {next(iter(questions)): value}
    return decide


def test_closing_transport_maps_probability_to_the_gate_contract(monkeypatch):
    monkeypatch.setattr(jev, "decide", _fake_decide(0.9))
    raw = _REAL_CLOSING_TRANSPORT(json.dumps({"bubbles": ["In welcher Region suchen Sie?"]}))
    assert json.loads(raw) == {"closes": True}
    monkeypatch.setattr(jev, "decide", _fake_decide(0.2))
    raw = _REAL_CLOSING_TRANSPORT(json.dumps({"bubbles": ["Alles klar, ich melde mich bei Ihnen."]}))
    assert json.loads(raw) == {"closes": False}


def test_closing_gate_end_to_end_on_jev(monkeypatch):
    """The gate's public contract is untouched: the same Verdicts the haiku era produced."""
    monkeypatch.setattr(CG, "_live_transport", _REAL_CLOSING_TRANSPORT)
    monkeypatch.setattr(jev, "decide", _fake_decide(0.5))
    assert CG.closes_the_turn(["In welcher Region suchen Sie?"]) == CG.Verdict(True, "model")
    monkeypatch.setattr(jev, "decide", _fake_decide(0.2))
    assert CG.closes_the_turn(["Das ist leider unterschiedlich."]) == CG.Verdict(False, "model")


def test_closing_transport_rejects_a_non_probability(monkeypatch):
    monkeypatch.setattr(jev, "decide", lambda s, q, **k: {"closes": "sure"})
    with pytest.raises(RuntimeError, match="no probability"):
        _REAL_CLOSING_TRANSPORT(json.dumps({"bubbles": ["x"]}))


def test_closing_gate_failure_direction_still_send_unchecked(monkeypatch):
    """A Jev outage sends the reply unchecked, never blocks it -- the gate's old asymmetry."""
    monkeypatch.setattr(CG, "_live_transport", _REAL_CLOSING_TRANSPORT)

    def boom(state, questions, **kw):
        raise jev.DecisionError("jev: HTTP 500")
    monkeypatch.setattr(jev, "decide", boom)
    v = CG.closes_the_turn(["In welcher Region suchen Sie?"])
    assert v.closes is True
    assert v.reason != "model"


def test_refusal_transport_maps_probability_to_the_gate_contract(monkeypatch):
    monkeypatch.setattr(jev, "decide", _fake_decide(0.9))
    raw = R._live_transport(json.dumps(
        {"candidate_reply": "nein, kein Interesse", "our_last_message": "Ist das fuer Sie interessant?"}))
    assert json.loads(raw) == {"unambiguous_refusal": True}
    monkeypatch.setattr(jev, "decide", _fake_decide(0.4))
    raw = R._live_transport(json.dumps({"candidate_reply": "vielleicht spaeter",
                                       "our_last_message": None}))
    assert json.loads(raw) == {"unambiguous_refusal": False}


def test_refusal_transport_rejects_a_non_probability(monkeypatch):
    monkeypatch.setattr(jev, "decide", lambda s, q, **k: {"unambiguous_refusal": "no"})
    with pytest.raises(RuntimeError, match="no probability"):
        R._live_transport(json.dumps({"candidate_reply": "x", "our_last_message": None}))


def test_refusal_gate_failure_direction_still_keep_talking(monkeypatch):
    """A Jev outage must land on the same side as before: not a refusal, keep the
    conversation, reason names the failure."""
    def boom(state, questions, **kw):
        raise jev.DecisionError("jev: HTTP 500")
    monkeypatch.setattr(jev, "decide", boom)
    v = R.is_unambiguous_refusal("nein, danke")
    assert v.is_refusal is False
    assert v.reason != "model"
