"""The Claude-driven brain: same transport and store as app/wa/brain.py, a different way of
deciding what to say. Where app/wa/brain.py picks the next question by how much it would
narrow the result set, this module asks Claude to decide -- with the persona, hard rules and
gates in app/wa/luna/prompts.py (see app/wa/luna/VENDORED.md for what those are adapted from
and why). Selected by WA_BRAIN=luna (app/wa/config.py); app/wa/api.py calls whichever brain is
configured through the same turn() contract, so the transport and the deterministic brain are
untouched by this module's existence.

The model is not asked to invent facts it cannot know. Region/qualification/housing gates are
enforced here in the harness (constitution.json, RULES) exactly like the reference this is
adapted from: the model picks the action and writes the wording, but three things are decided
in code, not by the model, because getting them wrong is either a compliance problem or an
unrecoverable false promise:

1. Opt-out (STOP) never reaches the model at all -- app/wa/api.py filters it before this
   module is called, same as the deterministic brain.
2. The first turn a candidate becomes not-placeable, the reject bubble is the locked German
   text (prompts.REJECT_BODY_DE), not the model's own phrasing -- matching the source's
   "process-exact wording, the model must not rephrase" rule for this exact gate.
3. A named region outside Bavaria gets the locked out-of-scope bubble
   (prompts.OUT_OF_SCOPE_REGION_DE), because this board has no data for anywhere else and a
   model-authored answer could imply otherwise.

Everything else -- tone, which question to ask, how to phrase the market snapshot, when to
escalate -- is the model's call, per turn, from the state this module hands it.
"""
import json
import pathlib
import re

from . import brain as B
from . import config as C
from . import slots as SL
from .luna import prompts as P

MAX_BUBBLES = 2
MATCH_LIMIT = 3

_LUNA_DIR = pathlib.Path(__file__).resolve().parent / "luna"
_CONSTITUTION_TEXT = json.dumps(json.loads((_LUNA_DIR / "constitution.json").read_text(encoding="utf-8")),
                                ensure_ascii=False, indent=2)
_QUALIFICATION_TEXT = json.dumps(json.loads((_LUNA_DIR / "qualification_knowledge.json").read_text(encoding="utf-8")),
                                 ensure_ascii=False, indent=2)

# Bundesländer this board has no data for. Named explicitly (not "everything but Bayern") so a
# misspelled or unrelated word never accidentally triggers the out-of-scope reply.
NON_BAVARIA_LAENDER = (
    "baden-württemberg", "baden-wurttemberg", "nordrhein-westfalen", "nrw", "niedersachsen",
    "hessen", "sachsen", "sachsen-anhalt", "thüringen", "thueringen", "brandenburg",
    "berlin", "hamburg", "bremen", "schleswig-holstein", "saarland", "rheinland-pfalz",
    "mecklenburg-vorpommern",
)


def _fold(text):
    return str(text or "").casefold()


def named_non_bavaria_land(text):
    """The out-of-scope Bundesland named in this message, or None. Whole-word (and not glued to
    a hyphen either), so 'Hessendorf' and 'NRW-Fan-Artikel' do not fire on 'Hessen'/'NRW'."""
    low = _fold(text)
    for land in NON_BAVARIA_LAENDER:
        if re.search(r"(?<![a-zäöü0-9-])" + re.escape(land) + r"(?![a-zäöü0-9-])", low):
            return land
    return None


# --- market snapshot: structured facts for the model, never rendered text -------------------
# The model writes the sentence; this only supplies what is true right now, from the same
# filter app/wa/brain.py and GET /api/jobs use, so the harness and the API cannot drift.

def market_snapshot(card):
    """-> {open_jobs, cities, consult, matches}. consult[] is a handful of live examples once
    role/region is known enough to be worth naming; matches[] is the narrower list once city
    or department is also known -- the two-stage shape the rules expect (name examples early,
    name matches once the CV/preferences narrow it down)."""
    filters = {}
    if card.get("qualification_path") not in (None, "reject"):
        filters["role"] = "pflegefachkraft"
    if card.get("city"):
        filters["city"] = card["city"]
    if card.get("department_pref"):
        filters["department"] = card["department_pref"]
    rows = B.jobs_for(filters)
    all_rows = B.jobs_for({})
    cities = B.known_cities()[:8]
    consult = [{"clinic": (r.get("clinic_name") or r.get("employer") or "").strip(),
                "city": (r.get("city") or r.get("clinic_town") or "").strip(),
                "department": r.get("department_hint")} for r in rows[:MATCH_LIMIT]]
    matches = []
    if card.get("city") and (card.get("department_pref") or card.get("qualification_path")):
        for r in rows[:MATCH_LIMIT]:
            matches.append({"clinic": (r.get("clinic_name") or r.get("employer") or "").strip(),
                            "city": (r.get("city") or r.get("clinic_town") or "").strip(),
                            "title": r.get("title"), "department": r.get("department_hint"),
                            "source_url": r.get("source_url") or r.get("external_url")})
    return {"open_jobs": len(all_rows), "cities": cities, "consult": consult, "matches": matches}


def requirement_scoreboard(card):
    """State only, never a script (RULES: 'the requirement scoreboard is state only'). One of
    satisfied | open | blocked per gate, so the model can see what is left without being told
    what to ask next."""

    def _q():
        path = card.get("qualification_path")
        if path in ("urkunde", "defizit", "kenntnispruefung"):
            return "satisfied"
        if path == "reject":
            return "blocked"
        return "open"

    return {
        "region": "satisfied" if card.get("region") else "open",
        "qualification": _q(),
        "city_or_department": "satisfied" if (card.get("city") or card.get("department_pref")) else "open",
        "housing": "satisfied" if card.get("housing_known") else "open",
        "handoff_consent": "satisfied" if card.get("anonymous_send_consent") else "open",
    }


# --- the Claude call -------------------------------------------------------------------------

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "bubbles": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
        "rationale": {"type": "string"},
        "escalate_to_manager": {"type": "boolean"},
        "escalate_reason": {"type": ["string", "null"]},
        "no_send": {"type": "boolean"},
        "next_ask": {"type": ["string", "null"]},
        "card_patch": {
            "type": "object",
            "properties": {
                "region": {"type": "string"},
                "city": {"type": "string"},
                "department_pref": {"type": "string"},
                "role_verdict": {"type": "string", "enum": ["accept", "reject", "unclear"]},
                "qualification_ok": {"type": "boolean"},
                "qualification_path": {"type": "string",
                                       "enum": ["urkunde", "defizit", "kenntnispruefung", "reject", "unknown"]},
                "urkunde_status": {"type": "string"},
                "housing_known": {"type": "boolean"},
                "people_count": {"type": "integer"},
                "pflege_matches_sent": {"type": "boolean"},
                "anonymous_send_offered": {"type": "boolean"},
                "anonymous_send_consent": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "required": ["action", "bubbles", "escalate_to_manager", "no_send", "card_patch"],
    "additionalProperties": False,
}
_REQUIRED_KEYS = tuple(OUTPUT_SCHEMA["required"])


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.S)


def _strip_fence(text):
    """A model answering through the CLI harness sometimes wraps JSON in a markdown code fence
    despite being told not to (observed on at least one model tier) -- strip it if present. This
    is normalization of a known formatting artifact, not a repair of malformed JSON: anything
    else wrong with the text still fails json.loads and raises."""
    m = _FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _validate(out):
    missing = [k for k in _REQUIRED_KEYS if k not in out]
    if missing:
        raise RuntimeError(f"Claude reply is missing required keys {missing}: {out!r}")
    if not isinstance(out.get("card_patch"), dict):
        raise RuntimeError(f"card_patch must be an object, got {out.get('card_patch')!r}")
    return out


class Client:
    """Runs the turn through the `claude` CLI's non-interactive print mode, not the Anthropic
    Python SDK -- so this rides whatever auth the CLI already has on the host (see
    app/wa/config.py:LUNA_CLAUDE_BIN) instead of needing a separate ANTHROPIC_API_KEY. Swappable
    (``reply=``) so tests never spawn a subprocess -- same seam as app/wa/meta.py:Client.

    The system prompt is passed with ``--system-prompt`` (a full replacement, not
    ``--append-system-prompt``): the CLI's own default coding-agent persona and tool
    instructions must not leak into a WhatsApp reply. ``--restricted`` removes the
    command/code-execution/WebFetch tools -- a conversational turn has no use for them, and
    without them there is nothing for the model to do but answer. The user payload goes over
    stdin rather than as a positional argument, so a long, growing thread history never risks
    an argument-length limit and never shows up in a process listing.
    """

    def __init__(self, reply=None):
        self._reply = reply or self._live_reply

    def _live_reply(self, system_text, user_text):
        import subprocess

        try:
            proc = subprocess.run(
                [C.LUNA_CLAUDE_BIN, "-p", "--restricted", "--output-format", "json",
                 "--model", C.LUNA_MODEL, "--effort", C.LUNA_EFFORT,
                 "--system-prompt", system_text],
                input=user_text, capture_output=True, text=True, timeout=C.LUNA_TIMEOUT_SEC,
            )
        except FileNotFoundError:
            raise RuntimeError(f"{C.LUNA_CLAUDE_BIN!r} is not on PATH -- WA_BRAIN=luna needs the "
                              f"Claude Code CLI installed and authenticated on this host")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude -p did not answer within {C.LUNA_TIMEOUT_SEC}s")
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
        return json.loads(_strip_fence(result))

    def reply(self, system_text, user_text):
        return _validate(self._reply(system_text, user_text))


def _user_payload(text, card, scoreboard, snapshot, history):
    return json.dumps({
        "latest_inbound": text,
        "card": card,
        "requirement_scoreboard": scoreboard,
        "market_snapshot": snapshot,
        "thread": history,
    }, ensure_ascii=False, sort_keys=True)


def _check(bubbles):
    if not (1 <= len(bubbles) <= MAX_BUBBLES):
        raise AssertionError(f"{len(bubbles)} bubbles, the style rule allows 1-{MAX_BUBBLES}")
    for b in bubbles:
        if not str(b or "").strip():
            raise AssertionError("empty bubble")
    return [str(b).strip() for b in bubbles]


def turn(text, thread, button_id=None, client=None, history=None):
    """Same contract as app/wa/brain.py:turn() -- {bubbles, buttons, slots, asked, stopped,
    matches, action} -- so app/wa/api.py can call either brain without knowing which one it got.
    ``slots`` here holds the Luna card (a different shape from the deterministic brain's slots;
    app/wa/store.py persists whatever dict it is given). ``asked`` is unused by this brain (the
    model tracks what it already asked via next_ask and the thread itself) and is passed through
    unchanged so the store's schema does not need to know which brain wrote a thread.
    """
    card = dict(thread.get("slots") or {})
    asked = list(thread.get("asked") or [])

    if SL.is_stop(text):
        return {"bubbles": [], "buttons": [], "slots": card, "asked": asked, "stopped": True,
                "matches": [], "action": "stopped"}

    land = named_non_bavaria_land(text)
    if land and not card.get("region"):
        card["region"] = land
        return {"bubbles": [P.OUT_OF_SCOPE_REGION_DE], "buttons": [], "slots": card, "asked": asked,
                "stopped": False, "matches": [], "action": "out_of_scope_region"}

    scoreboard = requirement_scoreboard(card)
    snapshot = market_snapshot(card)
    system_text = P.system_prompt(_CONSTITUTION_TEXT, _QUALIFICATION_TEXT)
    user_text = _user_payload(text, card, scoreboard, snapshot, history or [])

    cl = client or Client()
    out = cl.reply(system_text, user_text)

    patch = dict(out.get("card_patch") or {})
    was_ok = card.get("qualification_ok")
    card.update(patch)

    if patch.get("qualification_ok") is False and was_ok is not False:
        # The gate the model must not rephrase (prompts.py module docstring, point 2).
        bubbles = [P.REJECT_BODY_DE]
        action = "explain_not_placeable"
    else:
        bubbles = _check(out.get("bubbles") or [])
        action = str(out.get("action") or "reply_now_conversational")

    if out.get("escalate_to_manager"):
        card["_escalated"] = True
        card["_escalate_reason"] = out.get("escalate_reason")

    if out.get("no_send"):
        bubbles = []

    return {"bubbles": bubbles, "buttons": [], "slots": card, "asked": asked, "stopped": False,
            "matches": snapshot.get("matches") or [], "action": action}
