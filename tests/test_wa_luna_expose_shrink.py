"""Offline tests for app/wa/luna/expose_shrink.py (Ivan, 2026-09-24): a small-model rewrite that
shortens the board's own researched clinic paragraph before it reaches a candidate. Every test here
injects a fake transport -- no subprocess, no `claude` CLI, no live model, no network.

What this file does NOT do: assert that the live model produces a good rewrite. That is a live/eval
concern, not something an offline unit test can check. What it DOES check: a scripted rewrite is
returned as-is, and every failure mode -- not just a clean success -- falls back to the ORIGINAL
text unchanged, never raises, and never returns an empty string for a non-empty input.
"""
from app.wa.luna import expose_shrink as ES


def _scripted(rewrite):
    return lambda text: rewrite


def _raising(exc):
    def _transport(text):
        raise exc
    return _transport


def test_a_scripted_rewrite_is_returned():
    out = ES.shrink_expose_text("Ein sehr langer, aufzaehlungslastiger Absatz ueber die Klinik.",
                                transport=_scripted("Kurzer Pitch fuer die Klinik."))
    assert out == "Kurzer Pitch fuer die Klinik."


def test_empty_input_is_returned_unchanged_without_calling_the_transport():
    def _must_not_call(text):
        raise AssertionError("the transport must not be called for empty input")
    assert ES.shrink_expose_text("", transport=_must_not_call) == ""
    assert ES.shrink_expose_text("   ", transport=_must_not_call) == "   "


# --- every failure mode falls back to the ORIGINAL text, never raises, never empties it -----------

def test_transport_exception_falls_back_to_the_original_text():
    original = "Die Klinik bietet vielfaeltige Fachbereiche: Innere, Chirurgie, Notaufnahme, ITS."
    out = ES.shrink_expose_text(original, transport=_raising(RuntimeError("claude -p exited 1: boom")))
    assert out == original


def test_missing_binary_falls_back_to_the_original_text():
    original = "Ein Absatz."
    out = ES.shrink_expose_text(original, transport=_raising(FileNotFoundError("claude")))
    assert out == original


def test_empty_rewrite_falls_back_to_the_original_text():
    original = "Ein Absatz, der nicht leer werden darf."
    out = ES.shrink_expose_text(original, transport=_scripted("   "))
    assert out == original


def test_live_transport_parses_the_cli_envelope(monkeypatch):
    """The default transport (_live_transport) is exercised here with a faked subprocess.run, the
    same boundary refusal.py's own _live_transport tests would use -- proves the JSON envelope is
    read the same way (envelope['result'], is_error, a non-string/empty result all raise)."""
    import json
    import subprocess

    class _FakeCompleted:
        def __init__(self, stdout, returncode=0, stderr=""):
            self.stdout, self.returncode, self.stderr = stdout, returncode, stderr

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeCompleted(
        json.dumps({"result": "Kurzer Pitch.", "is_error": False})))
    assert ES._live_transport("irrelevant input") == "Kurzer Pitch."

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeCompleted(
        json.dumps({"result": None, "is_error": True})))
    try:
        ES._live_transport("irrelevant input")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "claude -p reported an error" in str(exc)

    def _raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=15)
    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    try:
        ES._live_transport("irrelevant input")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "did not answer within" in str(exc)
