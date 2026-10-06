"""Ivan, 2026-09-24: the clinic paragraph show_clinic_photos pulls from the board's own
Firecrawl-researched expose ("стянутые из expose параграфы") reads like a researched writeup, not a
sales pitch -- too long, too enumeration-heavy, no clear pitch for THIS candidate. This module
rewrites it, once per show_clinic_photos call, shorter and sales-first, before it becomes an image
caption or is handed to the main brain to write in its own words.

WHY A MODEL CALL RATHER THAN A TRUNCATION RULE: cutting the text at N characters or N sentences
would as often as not cut mid-fact or leave a dangling enumeration -- the same reasoning
app/wa/luna/refusal.py's docstring gives for not hand-rolling a decision that is really about
MEANING. Compressing prose so it still reads well and keeps the facts that sell the clinic is a
rewrite job, not a string operation.

THE ASYMMETRY (mirrors refusal.py's own): a wrongly-kept-too-long paragraph costs a slightly longer
message; a BLOCKED send because the shrinker hung or errored costs the whole funnel moment. So every
failure mode below -- a timeout, a missing `claude` binary, empty output, anything that is not usable
prose -- returns the ORIGINAL text unchanged, never raises, and never leaves the caller with nothing
to send. Each failure is logged at WARNING (not silently swallowed) so a shrinker outage is visible
without ever turning into a broken or missing message.

Runs through the same `claude` CLI mechanism as refusal.py's `_live_transport` (subprocess, `-p`,
`--output-format json`, no tools, no session) -- not a second way of calling a model.
"""
import json
import logging
import subprocess

from .. import config as C

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You receive one clinic description in German on stdin, researched for a nursing-recruitment "
    "board. Rewrite it SHORTER -- roughly half its length or less -- keeping the facts that would "
    "make a nurse or care worker want to work there (specialty, size, atmosphere, what makes it "
    "stand out), and drop everything else: long enumerations, secondary detail, generic filler. "
    "The job of the rewrite is to SELL this clinic to a candidate who is already looking, not to "
    "describe it exhaustively -- write it like a pitch, not a fact sheet. Keep it in German, keep "
    "the same register (informal Sie-form, as a recruiter would write), invent nothing not already "
    "in the text, and never add a question, a greeting, or anything that is not part of the "
    "description itself -- just the shortened paragraph, nothing else, no quotes, no preamble."
)


def _live_transport(text):
    """Runs ``claude -p`` once, statelessly, over stdin -- same mechanism as
    refusal.py's ``_live_transport``. Returns the raw output text; raises RuntimeError on anything
    that is not usable prose. Every raise here is turned into the original text by the caller."""
    try:
        proc = subprocess.run(
            [C.LUNA_CLAUDE_BIN, "-p", "--restricted", "--tools", "", "--output-format", "json",
             "--model", C.EXPOSE_SHRINK_MODEL, "--effort", "low", "--system-prompt", SYSTEM_PROMPT],
            input=text, capture_output=True, text=True, timeout=C.EXPOSE_SHRINK_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        raise RuntimeError(f"{C.LUNA_CLAUDE_BIN!r} is not on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"expose shrinker did not answer within {C.EXPOSE_SHRINK_TIMEOUT_SEC}s")
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude -p did not return JSON on stdout: {exc}: {proc.stdout[:300]!r}")
    if envelope.get("is_error"):
        raise RuntimeError(f"claude -p reported an error: {envelope.get('result')!r}")
    result = envelope.get("result")
    if not isinstance(result, str) or not result.strip():
        raise RuntimeError(f"claude -p returned no result text: {envelope!r}")
    return result.strip()


def shrink_expose_text(text, *, transport=None):
    """The shortened version of ``text``, or ``text`` itself unchanged on any failure -- never
    raises, never returns an empty string for a non-empty input. ``transport`` defaults to the live
    ``claude`` CLI call; tests inject a fake one (a scripted rewrite, or one that raises) so no test
    here spawns a subprocess or depends on a live model.

    Never call this with an empty ``text`` -- there is nothing to shrink and nothing to gain from a
    round trip; callers already only reach for a caption once one exists."""
    if not text or not text.strip():
        return text
    call = transport or _live_transport
    try:
        shortened = call(text)
    except Exception as exc:
        log.warning("expose shrinker failed, sending the original text unshortened: %s", exc)
        return text
    if not shortened.strip():
        log.warning("expose shrinker returned empty output, sending the original text unshortened")
        return text
    return shortened
