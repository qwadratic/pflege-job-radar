"""Is this inbound message an operational note to whoever maintains this system, rather than a turn in
a candidate conversation? (Ivan, 2026-09-24.)

WHY THIS EXISTS. Ivan and his partner test the rail by writing to it themselves, and they send their
corrections in Russian while testing the bot in German. Live, 2026-09-24 17:26 UTC: a Russian voice
note asking to re-send a broadcast ("отправь мне ее еще раз... убедиться, что на новой версии кода
все работает") was handed to the candidate brain, which answered it in German four times over twelve
minutes -- introducing itself as Valentina, apologising that the voice note would not play, and
finally asking whether he was looking for a job in Bayern. The instruction was never seen by anyone
who could act on it. This gate is the fork that stops that.

WHO IT RUNS FOR, AND WHY THAT IS THE WHOLE SAFETY STORY (Ivan, explicit): only a thread already marked
is_test (app/wa/luna/test_threads.py -- the operators' own numbers). For every real candidate this
feature does not exist: the caller never reaches this module, so no candidate can be pulled out of the
funnel by a misclassification, no stranger can make this number emit anything, and the text that
reaches the periodic worker can only have come from a number an operator deliberately marked. An
earlier draft ran the gate on every number and guarded it with an approval state instead; that is
strictly worse, because the population this rail recruits is substantially Russian-speaking -- "ваш
бот прислал мне одно и то же три раза" is a real nurse reporting a real bug, and routing her out of
the conversation loses her silently. is_test is the authority boundary; nothing downstream re-derives
it from the message.

TWO STAGES, THE CHEAP DETERMINISTIC ONE FIRST. has_cyrillic() is code because it is a FACT about
codepoints, not a judgement about meaning -- the line this repo already draws (app/wa/luna/refusal.py:
"a judgement about what a sentence MEANS is a model's job; a comparison against DATA stays in code").
It is a necessary condition, so an operator testing the funnel in German pays nothing and is answered
by the brain exactly as a candidate would be. What it costs: Russian typed in Latin letters is not
seen -- a false negative, the recoverable direction.

THE ASYMMETRY (same shape as refusal.py's, opposite conclusion about which way is cheap):
- A false POSITIVE (an operator's German test turn, or a Russian aside that was not an instruction,
  pulled out of the funnel) breaks the very test run the operator is in the middle of, and the reply
  they were waiting for never comes.
- A false NEGATIVE (a real instruction answered by the brain) costs one German non-sequitur to the one
  person best equipped to notice it in seconds, from a number that can simply resend.
So every failure mode -- no Cyrillic, timeout, missing binary, non-zero exit, unparseable output, a
non-boolean verdict -- resolves to Verdict(False, ...), logged at ERROR, and the message is answered
as an ordinary turn. Never a default that could flip the direction.

Runs through the same `claude` CLI mechanism app/wa/luna_brain.Client and refusal.py already use
(subprocess, `-p`, `--output-format json`, `--restricted --tools ""` so the classification itself can
reach nothing), not a second way of calling a model. Stateless: no --session-id/--resume.
"""
import json
import logging
import re

from .. import config as C
from .refusal import _extract_verdict_json, _run_cli

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You receive one JSON object on stdin: {\"message\": one WhatsApp message that just arrived on a "
    "German nursing-recruitment number, either typed or the transcript of a voice note}. The sender is "
    "known to be an OPERATOR of this system -- the people who built it -- testing it from their own "
    "phone. They do two different things on this number, and you decide which one this message is.\n"
    "(A) They role-play a candidate to test the bot. Those messages are in German and read like a "
    "nurse: asking about jobs, clinics, shifts, documents, housing, saying where they live or what "
    "qualification they hold.\n"
    "(B) They send an instruction or observation to the engineer who maintains the system: change "
    "something, check something, re-send something, fix something, deploy something, or a report of "
    "how the bot behaved wrongly. These are in Russian.\n"
    "Answer true only for (B): the message is written predominantly in Russian AND is addressed to "
    "the engineer rather than spoken in the role of a candidate.\n"
    "Answer false for (A), for any message in German or any language other than Russian, and for a "
    "Russian message that is still just role-play as a candidate talking about their own job search.\n"
    "A message that names parts of this system -- the bot, the funnel, the broadcast, the tests, the "
    "deploy, the database, the code, a message that went out wrong -- is (B). A message that only "
    "talks about the sender's own nursing career is (A), whatever language it is in.\n"
    "If you are genuinely unsure, answer false.\n"
    'Reply with ONLY this JSON object and nothing else: {"operator_note": true or false}'
)

# Cyrillic, plus the supplement block: a codepoint range, not a phrase list.
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿԀ-ԯ]")


class Verdict:
    """``is_note``: what the caller acts on. ``reason``: always set, for the caller to log -- "model"
    marks a real classifier answer, "no cyrillic" the stage-0 skip, anything else names the failure
    that forced ``is_note=False`` (see the asymmetry in this module's docstring)."""

    __slots__ = ("is_note", "reason")

    def __init__(self, is_note, reason):
        self.is_note = is_note
        self.reason = reason

    def __repr__(self):
        return f"Verdict(is_note={self.is_note!r}, reason={self.reason!r})"

    def __eq__(self, other):
        return isinstance(other, Verdict) and self.is_note == other.is_note and self.reason == other.reason


def has_cyrillic(text):
    """The necessary condition, decided in code before any model call: no Cyrillic, no gate. An
    operator testing the funnel in German never pays for a classifier call, and neither does anything
    else that reaches this module."""
    return bool(text) and bool(_CYRILLIC_RE.search(text))


def _live_transport(payload_text):
    """Runs ``claude -p`` once, statelessly, with ``payload_text`` over stdin -- same reasoning as
    refusal._live_transport: a long message never risks an argument-length limit or shows up in a
    process listing. Returns the raw result text; raises RuntimeError on anything that is not a usable
    answer. Every raise here is turned into Verdict(False, ...) by the caller."""
    return _run_cli(payload_text, model=C.AGENT_NOTE_MODEL, timeout_sec=C.AGENT_NOTE_TIMEOUT_SEC,
                    system_prompt=SYSTEM_PROMPT, what="agent-note gate")


def is_operator_note(text, *, transport=None):
    """The one entry point. Call it ONLY for a thread already marked is_test -- this module does not
    re-check that, and running it on a real candidate is the one failure mode the design has no
    recovery for (see the docstring).

    ``transport`` defaults to the live ``claude`` CLI call; tests inject a fake one -- ``lambda
    payload_text: '...'`` for a scripted answer, or one that raises, to exercise a failure path -- so
    no test here spawns a subprocess or depends on a live model. Always returns a Verdict; never
    raises."""
    if not has_cyrillic(text or ""):
        return Verdict(False, "no cyrillic")
    call = transport or _live_transport
    try:
        raw = call(json.dumps({"message": text}, ensure_ascii=False))
    except Exception as exc:
        log.error("agent-note gate call failed, treating as an ordinary candidate turn: %s", exc)
        return Verdict(False, f"transport failed: {exc}")
    try:
        parsed = _extract_verdict_json(raw)
    except ValueError as exc:
        log.error("agent-note gate returned unparseable output, treating as an ordinary turn: %s", exc)
        return Verdict(False, f"unparseable output: {raw[:300]!r}")
    if not isinstance(parsed, dict):
        log.error("agent-note gate answer was not a JSON object, treating as an ordinary turn: %r", parsed)
        return Verdict(False, f"ambiguous verdict: {parsed!r}")
    verdict = parsed.get("operator_note")
    if not isinstance(verdict, bool):
        log.error("agent-note gate answer had no boolean operator_note, treating as an ordinary turn: %r",
                  parsed)
        return Verdict(False, f"ambiguous verdict: {parsed!r}")
    return Verdict(verdict, "model")
