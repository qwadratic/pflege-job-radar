"""Offline tests for app/wa/luna/refusal.py (TASK-155, Ivan's rule 2026-09-22): a conversation ends
only on an unambiguous refusal, decided by a small-model call, never by hand-written German phrase
matching. Every test here injects a fake transport -- no subprocess, no `claude` CLI, no live model,
no network (this module is exempt from the `llm` marker in pytest.ini for exactly that reason).

What this file does NOT do: assert that the live model correctly reads any of the German phrases
below. That is a live/eval concern (see the workflow's own twelve-shape live run), not something an
offline unit test can check. What it DOES check: given a verdict (however the model reached it), the
mapping from that verdict to a decision is right, and every failure mode -- not just a clean false --
lands on KEEP TALKING. The phrase table documents, in one place, what a correct classifier is expected
to say about each shape; each row's fake transport returns exactly that expectation, so the table
doubles as the runnable spec.
"""
import json
import time

import pytest

from app import data as D
from app.wa import config as C
from app.wa import luna_brain as LB
from app.wa.luna import refusal as R


def _scripted(verdict):
    """A transport that answers as if the model had judged `verdict` -- the clean-JSON case."""
    return lambda text: json.dumps({"unambiguous_refusal": verdict})


def _raising(exc):
    def _transport(text):
        raise exc
    return _transport


def _constant(raw):
    return lambda text: raw


# --- verdict -> decision -------------------------------------------------------------------------

def test_true_verdict_ends_the_conversation():
    v = R.is_unambiguous_refusal("Kein Interesse mehr.", transport=_scripted(True))
    assert v == R.Verdict(True, "model")


def test_false_verdict_keeps_talking():
    v = R.is_unambiguous_refusal("Vielleicht, mal sehen.", transport=_scripted(False))
    assert v == R.Verdict(False, "model")


def test_markdown_fence_around_the_json_is_tolerated():
    fenced = "```json\n{\"unambiguous_refusal\": true}\n```"
    v = R.is_unambiguous_refusal("Nein danke, kein Interesse.", transport=_constant(fenced))
    assert v.is_refusal is True
    assert v.reason == "model"


def test_stray_prose_around_the_json_is_tolerated():
    prose = "Sure, here is the verdict: {\"unambiguous_refusal\": false} -- hope that helps."
    v = R.is_unambiguous_refusal("Erst nächstes Jahr.", transport=_constant(prose))
    assert v.is_refusal is False
    assert v.reason == "model"


# --- every failure mode means KEEP TALKING (the asymmetry) ---------------------------------------

def test_transport_exception_keeps_talking():
    v = R.is_unambiguous_refusal("egal", transport=_raising(RuntimeError("claude -p exited 1: boom")))
    assert v.is_refusal is False
    assert "transport failed" in v.reason


def test_missing_binary_keeps_talking():
    v = R.is_unambiguous_refusal("egal", transport=_raising(FileNotFoundError("claude")))
    assert v.is_refusal is False
    assert "transport failed" in v.reason


def test_timeout_keeps_talking():
    import subprocess
    v = R.is_unambiguous_refusal("egal", transport=_raising(subprocess.TimeoutExpired("claude", 20)))
    assert v.is_refusal is False
    assert "transport failed" in v.reason


def test_unparseable_output_keeps_talking():
    v = R.is_unambiguous_refusal("egal", transport=_constant("not json at all, sorry"))
    assert v.is_refusal is False
    assert "unparseable" in v.reason


def test_json_without_the_expected_key_keeps_talking():
    v = R.is_unambiguous_refusal("egal", transport=_constant(json.dumps({"something_else": True})))
    assert v.is_refusal is False
    assert "ambiguous verdict" in v.reason


def test_non_boolean_value_keeps_talking():
    v = R.is_unambiguous_refusal("egal", transport=_constant(json.dumps({"unambiguous_refusal": "yes"})))
    assert v.is_refusal is False
    assert "ambiguous verdict" in v.reason


def test_json_array_instead_of_object_keeps_talking():
    v = R.is_unambiguous_refusal("egal", transport=_constant("[true]"))
    assert v.is_refusal is False


def test_empty_output_keeps_talking():
    v = R.is_unambiguous_refusal("egal", transport=_constant(""))
    assert v.is_refusal is False


# --- the phrase table (Ivan's twelve shapes + the buried-refusal and gate-nein cases) -------------
# NOT_REFUSAL: anything that leaves the door open even slightly -- a maybe, a deferral, a conditional
# yes, a request for more information, a question back, or a bare "nein" with no context to say it
# refuses contact rather than answering a yes/no gate question. REFUSAL: a clear, final no to being
# contacted or offered a position at all, including one stated politely inside a longer sentence.
PHRASE_TABLE = [
    ("vielleicht", False),
    ("ich weiß noch nicht", False),
    ("kommt drauf an, was Sie haben", False),
    ("erst nächstes Jahr", False),
    ("ich bin gerade in Elternzeit", False),
    ("schicken Sie mir Infos", False),
    ("warum fragen Sie?", False),
    ("ja, aber nächsten Monat", False),
    ("vielleicht ja, vielleicht nein", False),
    ("Nein", False),  # bare "nein", no context -- could be answering a gate question, not a refusal
    ("Nein danke, kein Interesse.", True),
    ("Ich suche nicht mehr, bitte keine Nachrichten mehr.", True),
    ("Vielen Dank für die Nachricht, aber ehrlich gesagt habe ich inzwischen schon eine Stelle "
     "gefunden, Sie brauchen sich keine Mühe mehr zu machen.", True),  # refusal buried in pleasantries
]


@pytest.mark.parametrize("text,expected_refusal", PHRASE_TABLE)
def test_phrase_table_documents_expected_classification(text, expected_refusal):
    # The fake transport stands in for "the model judged this shape correctly" -- this test exercises
    # the plumbing (verdict -> Verdict) against the documented expectation, not the model's own
    # linguistic judgment (see module docstring).
    v = R.is_unambiguous_refusal(text, transport=_scripted(expected_refusal))
    assert v.is_refusal is expected_refusal
    assert v.reason == "model"


def test_phrase_table_covers_both_directions():
    assert any(expected is True for _, expected in PHRASE_TABLE)
    assert any(expected is False for _, expected in PHRASE_TABLE)


# --- wired into app/wa/luna_brain.py (TASK-156): the decline branch runs the classifier -------------
# Same offline seam as tests/test_wa_luna_brain.py -- a fake luna_brain.Client, no subprocess, no
# network -- plus ``R._live_transport`` patched so the module's own live-CLI default (used when
# turn() calls ``RF.is_unambiguous_refusal(text)`` with no explicit transport) never runs here either.

@pytest.fixture()
def luna(tmp_path, monkeypatch):
    D._snap.update({"at": time.time(), "jobs": [], "clinics": [], "by_clinic": {}, "facets": {},
                    "taxonomy": {}, "loading": False, "error": None})
    monkeypatch.setattr(D, "refresh", lambda: D._snap)
    monkeypatch.setattr(C, "SQLITE_PATH", tmp_path / "wa.sqlite")
    monkeypatch.setattr(C, "LUNA_SESSION_DIR", tmp_path / "wa_luna_sessions")
    return {"slots": {}, "asked": []}


def _out(**kw):
    base = {"action": "reply_now_conversational", "bubbles": ["Hallo 🙂"], "rationale": "",
            "escalate_to_manager": False, "escalate_reason": None, "no_send": False,
            "next_ask": None, "card_patch": {}}
    base.update(kw)
    return base


def fake_client(out_or_fn):
    fn = out_or_fn if callable(out_or_fn) else (lambda system, user, session_id: (out_or_fn, session_id))
    return LB.Client(reply=fn)


def test_a_soft_answer_with_decline_true_keeps_the_conversation_alive_and_records_it(luna, monkeypatch):
    """A model that raises decline=true on a soft/deferred answer is not enough on its own -- Ivan's
    rule ends a conversation only on an unambiguous refusal. The classifier disagreeing keeps the
    thread open (the model's own bubbles go out, card.declined stays unset) and the fact that a
    decline was refused this way is recorded on the card, so a human can see it happened."""
    monkeypatch.setattr(R, "_live_transport", lambda text: json.dumps({"unambiguous_refusal": False}))
    out = _out(decline=True, decline_reason="model misread a maybe as a no",
               bubbles=["Kein Problem, lassen Sie sich Zeit."])
    d = LB.turn("vielleicht, mal sehen", luna, client=fake_client(out))
    assert d["action"] != "decline_ack" and d["bubbles"] == out["bubbles"]
    assert d["slots"].get("declined") is not True
    assert d["slots"]["_escalated"] is True
    assert "classifier disagreed" in d["slots"]["_escalate_reason"]


def test_an_unambiguous_refusal_still_ends_the_conversation(luna, monkeypatch):
    monkeypatch.setattr(R, "_live_transport", lambda text: json.dumps({"unambiguous_refusal": True}))
    out = _out(decline=True, decline_reason="no interest", bubbles=["Schade, alles Gute!"])
    d = LB.turn("Nein danke, ich habe schon eine andere Stelle gefunden.", luna, client=fake_client(out))
    assert d["action"] == "decline_ack" and d["bubbles"] == [LB.P.DECLINE_ACK_DE]
    assert d["slots"]["declined"] is True


def test_a_classifier_failure_on_the_decline_path_also_keeps_talking_and_is_recorded(luna, monkeypatch):
    """Every classifier failure mode (refusal.py's asymmetry) means NOT a refusal, exactly like a
    genuine disagreement -- and is recorded with its own reason, so a silent classifier outage (a
    missing binary, a timeout) is visible on the thread rather than looking like nothing happened."""
    def _boom(text):
        raise RuntimeError("claude -p exited 1: boom")
    monkeypatch.setattr(R, "_live_transport", _boom)
    out = _out(decline=True, bubbles=["Ok, danke fuer die Rueckmeldung."])
    d = LB.turn("kommt drauf an", luna, client=fake_client(out))
    assert d["slots"].get("declined") is not True
    assert d["slots"]["_escalated"] is True
    assert "transport failed" in d["slots"]["_escalate_reason"]


def test_the_classifier_runs_only_on_the_decline_path_never_on_every_message(luna, monkeypatch):
    calls = []

    def _spy(text):
        calls.append(text)
        return json.dumps({"unambiguous_refusal": False})

    monkeypatch.setattr(R, "_live_transport", _spy)
    LB.turn("Ja, gerne", luna, client=fake_client(_out(bubbles=["Klar, gerne!"])))
    assert calls == [], "the refusal classifier must not run on an ordinary (non-decline) turn"
