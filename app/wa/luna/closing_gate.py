"""Does the reply the brain just wrote actually hand the turn back to the candidate? (Ivan, 2026-09-24.)

THE ONE INVARIANT, in Ivan's words: "каждый ответ модели -- это массив баблов, в котором последний
закрывающий". Every reply is an array of bubbles; the LAST bubble closes the turn. Not a rule per
bubble, not a rule per conversation stage -- one rule, checked once, on the array the brain is about
to send.

WHY. Valentyn, live 2026-09-24, asked "Wo ist die Klinik?" three times running and got three
different phrasings of the same non-specific answer while his card stayed completely empty. A real
candidate shows the same disease from the other side: 21 outbound messages against
7 inbound, 8 of them the fixed "sind Sie noch da?" nudge, and one single slot ever filled. Both are
the same failure -- we said something true and then stopped, leaving the person with nothing to
answer.

WHAT THIS REPLACES. The first attempt at this (the same day) asserted that the model's own
``next_ask`` field was non-empty whenever requirement_scoreboard reported an open gate. Ivan rejected
it, correctly: it checked a field the model fills in about itself rather than the text that actually
goes out, and it branched on the scoreboard, so a turn with no open gate was exempt from the only
rule that matters. This gate reads the bubbles.

WHAT "CLOSING" MEANS is a judgement about what a sentence DOES, so it is a model's job, not a regex
-- the line this repo already draws (refusal.py). Two very different-looking bubbles both close:
"In welcher Region suchen Sie?" and "Ich habe Ihnen alles Wichtige weitergegeben, eine Kollegin
meldet sich morgen bei Ihnen." One asks, one ends the conversation on purpose. What does NOT close is
a bubble that only states, explains, apologises or promises and leaves the candidate nothing to
answer. Writing that distinction into the gate's prompt is what keeps the CODE free of a branch per
terminal stage (decline, not placeable, consent, handoff) -- Ivan: "без разветвлений, без ничего".

THE ASYMMETRY, opposite to agent_note_gate.py's and deliberately so. This gate is a QUALITY check
standing in front of a candidate who is waiting, not an authority boundary. So every failure --
timeout, missing binary, non-zero exit, unparseable output, a non-boolean verdict -- resolves to
Verdict(True, ...): the reply goes out UNCHECKED, logged at ERROR. A gate that failed closed would
turn one slow Haiku call into a silent rail, which is strictly worse than one German message that
forgot to ask a question. An empty bubble list is the same: nothing to close, nothing to check.

ON FAILURE THE HARNESS DOES NOT ESCALATE (Ivan, explicit: "нет такого, что мы каждое сообщение
проверяем на гейт, а потом зовем человека"). The caller re-runs the SAME turn once, in the SAME
session, with a short hint -- not the violation quoted back, not a new set of instructions -- and
sends whatever comes back. See app/wa/luna_brain.py::_checked_reply.

A STRICTNESS GRADIENT WAS BUILT HERE AND THEN REMOVED THE SAME DAY. Ivan first asked for the gate to
soften as a candidate neared the end, which sounds obviously right: late in the funnel the person
knows what is expected, so a summary or a reassurance reads like a fine way to finish. It was
implemented as two integers in the payload (settled gates of total) with the gradient in this
prompt. An adversarial review then showed it was precisely backwards, and Ivan agreed and had it
taken out. The reason is structural, not a matter of taste: ``handoff_consent`` is the only gate in
requirement_scoreboard with no ``blocked`` state, so "one gate left" is BY CONSTRUCTION the consent
stage and almost nothing else. The most lenient bucket therefore switched on exactly and only at the
one ask that produces the outcome, where "Super, dann habe ich alles zusammen." would have passed
and the close would never have been asked for. Do not reintroduce it without solving that: being
near the end is not a licence to skip the final question, it is the moment the final question
matters most.

Runs through the same `claude` CLI mechanism luna_brain.Client, refusal.py and agent_note_gate.py
use (subprocess, `-p`, `--output-format json`, `--restricted --tools ""`), not a second way of
calling a model. Stateless: no --session-id/--resume, and the candidate's history is never sent --
the payload is the outgoing bubbles and nothing else.
"""
import json
import logging

from .. import config as C
from .refusal import _extract_verdict_json, _run_cli

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are the last check a message passes before it reaches a real person.\n"
    "\n"
    "WHY YOU EXIST. On the other side of this chat is a nurse looking for work in Bavaria, reading "
    "on their phone, often between shifts. A recruiting assistant can only help them if the "
    "conversation keeps moving, and on WhatsApp a conversation only keeps moving while each message "
    "hands the turn back. A message can be true, warm and completely correct and still kill the "
    "conversation, simply by leaving the person nothing to answer: they read it, there is nothing to "
    "reply to, and they never write again. That is not hypothetical here -- someone asked the same "
    "question three times, got three polite non-answers, and in the end nothing about them had been "
    "learned and nobody could help them. You exist to catch that one failure, and only that one.\n"
    "\n"
    "WHAT YOU RECEIVE, one JSON object on stdin: {\"bubbles\": the short German WhatsApp messages "
    "the assistant is about to send, in the order they will be sent}.\n"
    "\n"
    "WHAT TO JUDGE: does the LAST bubble close the turn? Only the last one -- that is the one they "
    "read last and the one they answer, so an earlier bubble asking a good question does not rescue "
    "a final bubble that trails off.\n"
    "A last bubble CLOSES the turn when it hands the conversation back with something concrete to do "
    "or answer -- a direct question, a request for a document or a photo, a prompt to tap a button or "
    "reply with a word, a next step it asks them to confirm. It ALSO closes the turn when it "
    "deliberately ENDS the conversation: saying warmly that we cannot place them, accepting that they "
    "are not looking, or telling them a human colleague will take it from here. A conversation that "
    "is meant to be over is closed, not stalled.\n"
    "A last bubble does NOT close the turn when it only states, explains, apologises, thanks or "
    "promises and leaves the person nothing to answer -- the kind of message after which nothing "
    "happens unless they happen to write again unprompted.\n"
    "Judge what the sentence DOES, not how polite or long it is, and not whether you would have "
    "written it that way.\n"
    "\n"
    "BE EQUALLY STRICT AT EVERY POINT IN THE CONVERSATION. A late turn is not a turn that has earned "
    "the right to trail off -- it is usually the turn where one last thing still has to be asked for, "
    "and that is where an unanswered message costs the most.\n"
    "\n"
    "A false answer costs the assistant one rewrite. A wrong false answer costs the person a message "
    "written in a hurry. If you are genuinely unsure, answer true.\n"
    'Reply with ONLY this JSON object and nothing else: {"closes": true or false}'
)


class Verdict:
    """``closes``: what the caller acts on. ``reason``: always set, for the caller to log -- "model"
    marks a real classifier answer, "no bubbles" the nothing-to-check skip, anything else names the
    failure that forced ``closes=True`` (see the asymmetry in this module's docstring)."""

    __slots__ = ("closes", "reason")

    def __init__(self, closes, reason):
        self.closes = closes
        self.reason = reason

    def __repr__(self):
        return f"Verdict(closes={self.closes!r}, reason={self.reason!r})"

    def __eq__(self, other):
        return isinstance(other, Verdict) and self.closes == other.closes and self.reason == other.reason


def _live_transport(payload_text):
    """Runs ``claude -p`` once, statelessly, with ``payload_text`` over stdin -- same reasoning as
    refusal._live_transport. Returns the raw result text; raises RuntimeError on anything that is not
    a usable answer. Every raise here is turned into Verdict(True, ...) by the caller."""
    return _run_cli(payload_text, model=C.CLOSING_GATE_MODEL, timeout_sec=C.CLOSING_GATE_TIMEOUT_SEC,
                    system_prompt=SYSTEM_PROMPT, what="closing gate")


def closes_the_turn(bubbles, *, transport=None):
    """The one entry point. ``bubbles`` is the list of candidate-facing strings about to be sent --
    and that is the whole input: no card, no stage, no history, nothing that could make the same
    reply acceptable in one conversation and not in another.

    ``transport`` defaults to the live ``claude`` CLI call; tests inject a fake one -- ``lambda
    payload_text: '...'`` for a scripted answer, or one that raises, to exercise a failure path -- so
    no test here spawns a subprocess or depends on a live model. Always returns a Verdict; never
    raises."""
    texts = [str(b) for b in (bubbles or []) if str(b).strip()]
    if not texts:
        return Verdict(True, "no bubbles")
    call = transport or _live_transport
    try:
        raw = call(json.dumps({"bubbles": texts}, ensure_ascii=False))
    except Exception as exc:
        log.error("closing gate call failed, sending the reply unchecked: %s", exc)
        return Verdict(True, f"transport failed: {exc}")
    try:
        parsed = _extract_verdict_json(raw)
    except ValueError as exc:
        log.error("closing gate returned unparseable output, sending the reply unchecked: %s", exc)
        return Verdict(True, f"unparseable output: {raw[:300]!r}")
    if not isinstance(parsed, dict):
        log.error("closing gate answer was not a JSON object, sending the reply unchecked: %r", parsed)
        return Verdict(True, f"ambiguous verdict: {parsed!r}")
    verdict = parsed.get("closes")
    if not isinstance(verdict, bool):
        log.error("closing gate answer had no boolean closes, sending the reply unchecked: %r", parsed)
        return Verdict(True, f"ambiguous verdict: {parsed!r}")
    return Verdict(verdict, "model")
