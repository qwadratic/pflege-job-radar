"""TASK-155 (Ivan, 2026-09-22): is the candidate's own text an unambiguous refusal to continue the
conversation?

WIRED IN (TASK-156). The call site this module was built for -- and the only place it runs, never on
every inbound turn (cost shape, decided) -- is app/wa/luna_brain.py, function turn(), the decline
branch: a ``decline_candidate`` (out.get("decline") and not was_declined and not consent_no_tap) runs
``is_unambiguous_refusal(text)`` and only takes the decline branch (P.DECLINE_ACK_DE, card.declined)
when ``.is_refusal`` is also true. A ``decline=true`` the classifier disagrees with -- including every
failure mode below -- is treated like any other turn (KEEP TALKING, per prompts.py's "NOT A DECLINE,
KEEP GOING" rule) and recorded on the card (card._escalated/_escalate_reason) so a human sees it and a
silent classifier outage is visible on the thread, not only in the logs.

WHY A SECOND MODEL CALL RATHER THAN CODE (Ivan, explicit): a hand-written German phrase/regex/keyword
list to recognise a refusal was rejected on purpose -- language is unpredictable, and this repo has
already paid for that lesson once. app/wa/luna/grounding.py's NO INVENTION check took four rounds of
surface-pattern fixes; round three enumerated the prepositions bei/beim/im/in and the live model
promptly wrote "alle am Klinikum", silencing a truthful reply while three fabricated claims slipped
through the same patch (see grounding.py's own history). A judgement about what a sentence MEANS is a
model's job; a comparison against DATA (is this clinic on the board, is this number one a tool
returned) stays in code. Recognising a refusal is squarely the first kind of question, so it goes to a
model here too -- a small one (C.REFUSAL_MODEL, the Haiku tier), because the question is narrow and
answered in isolation, with no conversation history and no board tools to reason over.

THE ASYMMETRY THAT SETS THE FAILURE DIRECTION (Ivan): a wrongly-ended conversation is a lost candidate
and nobody ever learns it happened; a wrongly-continued one costs one more polite message. So every
failure mode below -- a timeout, a missing `claude` binary, output that is not the expected JSON, or a
verdict the model itself could not give as a clean boolean -- resolves to ``Verdict(is_refusal=False,
reason=...)``, never a silent default that could flip the direction. Each failure is logged at ERROR
(not swallowed) so a classifier outage is visible in the logs rather than quietly turning into "every
soft answer looks unambiguous" -- the one failure mode this design must not have.

Runs through the same `claude` CLI mechanism app/wa/luna_brain.Client already uses (subprocess, `-p`,
`--output-format json`), not a second way of calling a model -- see C.LUNA_CLAUDE_BIN. Unlike that
client this call is stateless (no `--session-id`/`--resume`): a refusal check is a one-shot
classification of one message, not a turn in a remembered conversation.

THE MISSING INPUT (TASK-157, Ivan, 2026-09-22 -- a same-day regression on the wiring above). Measured
against the live classifier right after TASK-156 wired this in: a bare "Nein" and "Nein, danke" came
back NOT a refusal even right after the campaign opener that asks whether the candidate's job search is
still relevant -- exactly the shape TASK-101/TASK-105 exist to end. The classifier was obeying its own
prompt correctly: a bare "nein" genuinely can be answering a yes/no gate question (a document, a region,
a city, housing), and with no idea what was asked, "genuinely unsure, answer false" is the only honest
verdict. The fix is not a policy change -- it is giving the classifier the one fact it was missing:
``our_last_message`` (still optional, still keyword-only) is the text of the last WhatsApp message we
ourselves sent this candidate, or ``None`` when there is none (a first-ever inbound with no prior
outbound at all -- SYSTEM_PROMPT tells the model to fall back to reading candidate_reply alone then,
exactly as before this fix, never a bias toward ending the conversation on missing information). The
caller (app/wa/luna_brain.py:turn()) reads it off the thread's own record of what actually went out
(turn_context()'s ``last_outbound``), not off the model's own memory -- this call stays stateless. The
distinction our_last_message is used for is narrow and singular, per SYSTEM_PROMPT: was our last message
a narrow yes/no gate question about ONE specific thing, so a short negative answers it, or was it
anything broader (the campaign opener included) or unknown, so a short negative has nothing to answer
and reads as the plain refusal phrase it is. Every failure mode still resolves to NOT a refusal; this
input only ever sharpens the true/false line between two readings of a short negative, never widens what
counts as an unambiguous refusal in the first place.
"""
import json
import logging
import re
import subprocess

from .. import config as C

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You receive one JSON object on stdin: {\"our_last_message\": the WhatsApp text we ourselves sent "
    "this nursing-recruitment candidate right before their reply, or null when we do not know it; "
    "\"candidate_reply\": their reply, in German or any other language}. Answer exactly one question "
    "about candidate_reply: does it unambiguously refuse to continue the conversation -- a clear, final "
    "no to being contacted or offered a position at all (e.g. 'kein Interesse', 'nicht mehr', 'habe "
    "schon eine Stelle', a hard stop)?\n"
    "Anything that leaves the door open even slightly is NOT an unambiguous refusal: a maybe, a "
    "deferral ('vielleicht später', 'erst nächstes Jahr'), a conditional yes, a request for more "
    "information before deciding, or a question back. If you are genuinely unsure, answer false.\n"
    "our_last_message, when it is not null, decides exactly one distinction: whether a short negative on "
    "its own ('nein', 'nein danke', 'nein, kein Interesse') is ANSWERING us or REFUSING us. Ask: was "
    "our_last_message requesting one FACT about the candidate themself -- do they hold a specific "
    "document, are they in a specific region, city, or do they need housing -- something they could "
    "answer with a fact that is true of them regardless of whether they want this job at all? Or was it "
    "asking about their INTEREST, willingness or consent to be contacted or continue at all (e.g. 'is "
    "this relevant/interesting for you', 'may we contact you', any general opener or offer)?\n"
    "- A FACT question: the short negative states that fact -- NOT a refusal. Example: 'Haben Sie die "
    "Urkunde schon?' / 'Suchen Sie in Bayern?' -> 'nein' answers it, keep talking.\n"
    "- An INTEREST/willingness/consent question, or our_last_message is anything else that is not one "
    "narrow FACT question: the short negative refuses that interest/willingness/consent outright -- an "
    "unambiguous refusal. Example: 'Ist das fuer Sie interessant?' / 'Suchen Sie noch eine Stelle?' -> "
    "'nein' or 'nein danke' ends it.\n"
    "- our_last_message is null (we do not know what we last sent): a short negative alone has nothing "
    "to read it against -- stay with 'if genuinely unsure, answer false' above, exactly as you would if "
    "this whole field did not exist.\n"
    "Every other shape stays governed by the paragraph above regardless of our_last_message: a maybe, a "
    "deferral, a conditional yes, a request for more information, a question back, or any reply longer "
    "than a bare short negative -- read those on their own merits.\n"
    'Reply with ONLY this JSON object and nothing else: {"unambiguous_refusal": true or false}'
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.S)


class Verdict:
    """``is_refusal``: what the caller should act on. ``reason``: always set, for the caller to log --
    ``"model"`` marks a real classifier answer; anything else names the failure that forced
    ``is_refusal=False`` (see the asymmetry in this module's docstring)."""

    __slots__ = ("is_refusal", "reason")

    def __init__(self, is_refusal, reason):
        self.is_refusal = is_refusal
        self.reason = reason

    def __repr__(self):
        return f"Verdict(is_refusal={self.is_refusal!r}, reason={self.reason!r})"

    def __eq__(self, other):
        return (isinstance(other, Verdict) and self.is_refusal == other.is_refusal
                and self.reason == other.reason)


def _live_transport(payload_text):
    """Runs ``claude -p`` once, statelessly, ``payload_text`` (the JSON envelope
    ``is_unambiguous_refusal`` builds -- ``our_last_message``/``candidate_reply``) over stdin -- same
    reasoning as app/wa/luna_brain.Client._live_reply: a long message never risks an argument-length
    limit or shows up in a process listing. Returns the raw result text; raises RuntimeError on
    anything that is not a usable answer. Every raise here is turned into Verdict(False, ...) by the
    caller, never left to propagate -- see the asymmetry in the module docstring."""
    try:
        proc = subprocess.run(
            [C.LUNA_CLAUDE_BIN, "-p", "--restricted", "--tools", "", "--output-format", "json",
             "--model", C.REFUSAL_MODEL, "--effort", "low", "--system-prompt", SYSTEM_PROMPT],
            input=payload_text, capture_output=True, text=True, timeout=C.REFUSAL_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        raise RuntimeError(f"{C.LUNA_CLAUDE_BIN!r} is not on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"refusal classifier did not answer within {C.REFUSAL_TIMEOUT_SEC}s")
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
    return result


def _extract_verdict_json(text):
    """Same tolerance app/wa/luna_brain._parse_reply_json applies to the main brain's replies: a
    model may wrap the JSON object in a markdown fence or stray prose despite the system prompt
    saying not to. Tried in order: as-is, fence stripped, the substring between the first ``{`` and
    the last ``}``. Raises ValueError if none of that yields a JSON object -- never guesses at one."""
    fenced = _FENCE_RE.match(text.strip())
    stripped = fenced.group(1).strip() if fenced else text.strip()
    for candidate in (text, stripped):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(stripped[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"no JSON object found in {text[:300]!r}")


def is_unambiguous_refusal(candidate_text, *, our_last_message=None, transport=None):
    """The one entry point. ``our_last_message`` (TASK-157) is the text of the last WhatsApp message we
    ourselves sent this candidate -- what the caller's turn_context/``last_outbound`` records, never the
    model's own memory (this call stays stateless) -- or ``None`` when there is none (a first-ever
    inbound with no prior outbound: SYSTEM_PROMPT then reads candidate_text alone, exactly as before this
    parameter existed). It sharpens exactly one distinction (SYSTEM_PROMPT): a short negative answering a
    narrow yes/no gate question we just asked, versus the same words refusing anything broader (the
    campaign opener included) or asked with no known context -- it never widens what counts as an
    unambiguous refusal in the first place.

    ``transport`` defaults to the live ``claude`` CLI call (``_live_transport``); tests inject a fake one
    -- ``lambda payload_text: '...'`` for a scripted answer, or one that raises, to exercise a failure
    path -- so no test here spawns a subprocess or depends on a live model. ``transport`` receives the
    JSON envelope ``{"our_last_message": ..., "candidate_reply": ...}`` as one string, the same one
    ``_live_transport`` pipes to the CLI over stdin. Always returns a Verdict; never raises (every failure
    is caught and turned into ``Verdict(False, reason)``, per the asymmetry in the module docstring)."""
    call = transport or _live_transport
    payload = json.dumps({"our_last_message": our_last_message, "candidate_reply": candidate_text},
                         ensure_ascii=False)
    try:
        raw = call(payload)
    except Exception as exc:
        log.error("refusal classifier call failed, defaulting to NOT a refusal (keep talking): %s", exc)
        return Verdict(False, f"transport failed: {exc}")
    try:
        parsed = _extract_verdict_json(raw)
    except ValueError as exc:
        log.error("refusal classifier returned unparseable output, defaulting to NOT a refusal: %s", exc)
        return Verdict(False, f"unparseable output: {raw[:300]!r}")
    if not isinstance(parsed, dict):
        log.error("refusal classifier answer was not a JSON object, defaulting to NOT a refusal: %r", parsed)
        return Verdict(False, f"ambiguous verdict: {parsed!r}")
    verdict = parsed.get("unambiguous_refusal")
    if not isinstance(verdict, bool):
        log.error("refusal classifier answer had no boolean unambiguous_refusal, defaulting to NOT a "
                  "refusal: %r", parsed)
        return Verdict(False, f"ambiguous verdict: {parsed!r}")
    return Verdict(verdict, "model")
