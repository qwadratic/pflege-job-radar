"""The Claude-driven brain: same transport and store as app/wa/brain.py, a different way of
deciding what to say. Where app/wa/brain.py picks the next question by how much it would
narrow the result set, this module asks Claude to decide -- with the persona, hard rules and
gates in app/wa/luna/prompts.py (see app/wa/luna/VENDORED.md for what those are adapted from
and why). Selected by WA_BRAIN=luna (app/wa/config.py); app/wa/api.py calls whichever brain is
configured through the same turn() contract, so the transport and the deterministic brain are
untouched by this module's existence.

Each WhatsApp thread is one persistent Claude Code session (``--session-id`` on first contact,
``--resume`` on every later turn), not a stateless call replaying the whole thread as one blob
each time. The conversation itself -- what was said, in what order -- lives in that session, the
same way a human's memory of a chat does. What this module sends each turn is only what a human
recruiter would have to re-check every time anyway: the candidate's latest message, and the
current, possibly-just-changed ground truth (the state card, and a market snapshot from data that
moves independently of the conversation -- new postings appear, old ones close). Session id is
kept on the thread's own card (``_session_id``), so it survives a process restart the same way
the rest of the card does.

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
import sys
import uuid

from . import brain as B
from . import config as C
from . import slots as SL
from .luna import prompts as P

MAX_BUBBLES = 2

# Explicit button-confirmed consent (TASK-80): the real reference system has this same rigor for
# its own harder, named-clinic-submission gate (a tappable "Ja, ich bestätige"), never inferred
# from free text. This harness has no named-submission step, but applies the same discipline to
# the one consent point it does have -- anonymous_send_consent is set ONLY by a genuine tap on one
# of these two buttons (see turn(), below), never trusted from the model's own card_patch.
CONSENT_YES_ID = "consent:yes"
CONSENT_NO_ID = "consent:no"
CONSENT_BUTTONS = [{"id": CONSENT_YES_ID, "title": "Ja, gerne"}, {"id": CONSENT_NO_ID, "title": "Nein danke"}]

_LUNA_DIR = pathlib.Path(__file__).resolve().parent / "luna"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONSTITUTION_TEXT = json.dumps(json.loads((_LUNA_DIR / "constitution.json").read_text(encoding="utf-8")),
                                ensure_ascii=False, indent=2)
_QUALIFICATION_TEXT = json.dumps(json.loads((_LUNA_DIR / "qualification_knowledge.json").read_text(encoding="utf-8")),
                                 ensure_ascii=False, indent=2)

# The three tools Luna herself may call, as the CLI names an external MCP tool
# (mcp__<server-name>__<tool-name> -- confirmed live, this is not documented anywhere formal).
# tools_server.py also defines get_clinic_contact (with its own tests) -- deliberately NOT in this
# tuple (TASK-91): contact details are for the human handoff after consent (app/wa/queue.py), never
# something the candidate-facing conversation itself should be able to surface.
MCP_SERVER_NAME = "pflege_board"
MCP_TOOL_NAMES = tuple(f"mcp__{MCP_SERVER_NAME}__{t}" for t in
                       ("search_postings", "get_posting", "list_clinics"))


def _mcp_config_path():
    """Write (once per process) the --mcp-config file pointing the CLI at tools_server.py,
    launched with the same interpreter this process runs under -- that interpreter is guaranteed
    to have the `mcp` package installed, whereas a bare `python`/`python3` on PATH might be a
    different, unrelated interpreter.

    Every luna turn runs with ``cwd=C.LUNA_SESSION_DIR`` (a data directory, not the repo root --
    required for Claude Code's own session-resume-by-cwd behavior, see the Client docstring), and
    the per-server ``cwd`` field in this config was found NOT to be honored by the CLI's stdio MCP
    launcher (verified live: the spawned server still inherited the outer process's cwd and failed
    with ``ModuleNotFoundError: No module named 'app'``). ``env.PYTHONPATH`` is what actually makes
    ``python -m app.wa.luna.tools_server`` resolve regardless of the server's real working
    directory, so that -- not ``cwd`` -- is the field this depends on; ``cwd`` is left in too since
    a future CLI version honoring it would only help, never hurt.

    The server is also a fresh subprocess for config purposes: it does its own import of
    app.wa.config, so a test's ``monkeypatch.setattr(config, "SQLITE_PATH"/"LUNA_SESSION_DIR", ...)``
    on *this* process never reaches it on its own. WA_SQLITE_PATH/WA_LUNA_SESSION_DIR pass this
    process's current values through explicitly so a test board and a test tool-call log are the
    same one on both sides of the subprocess boundary (see tools_server.py's own handling)."""
    C.LUNA_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = C.LUNA_SESSION_DIR / "mcp_config.json"
    config = {"mcpServers": {MCP_SERVER_NAME: {"command": sys.executable,
                                                "args": ["-m", "app.wa.luna.tools_server"],
                                                "cwd": str(_REPO_ROOT),
                                                "env": {"PYTHONPATH": str(_REPO_ROOT),
                                                        "WA_SQLITE_PATH": str(C.SQLITE_PATH),
                                                        "WA_LUNA_SESSION_DIR": str(C.LUNA_SESSION_DIR)}}}}
    path.write_text(json.dumps(config), encoding="utf-8")
    return path

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

CLOSE_LIMIT = 5


def _city_or_department_satisfied(card):
    """The one predicate for the 'city_or_department' gate -- shared by requirement_scoreboard()
    and market_snapshot() so they cannot silently disagree about what counts as answered (a real
    bug found live, TASK-82: market_snapshot's ready_to_close used to require BOTH city AND
    department_pref while requirement_scoreboard told the model this gate was satisfied by EITHER
    -- a candidate who is genuinely flexible on department (a real, valid answer, not a missing
    one) then saw requirement_scoreboard say 'satisfied' while market_snapshot never actually
    produced a shortlist to close with, stalling the conversation indefinitely)."""
    return bool(card.get("city") or card.get("department_pref"))


def _documents_satisfied(card):
    """TASK-91: has the harness actually downloaded and read a document from this candidate
    (app/wa/api.py:_ingest_media sets cv_text or urkunde_text onto the card, TASK-67) -- a
    conversational 'ja, ich habe die Urkunde' alone does not satisfy this. Modeled on the real
    reference implementation's own document-ask gate (recon, TASK-91 notes): confirmed there that
    role+qualification+city+housing are prerequisites for the document ask, and the actual
    hand-off only fires once a document has been read, not merely claimed."""
    return bool(card.get("cv_text") or card.get("urkunde_text"))


def market_snapshot(card):
    """-> {open_jobs, cities, matching_clinics_count, shortlist, matches}. Deliberately thin
    (TASK-91, after reading how the real reference implementation actually works): open_jobs is
    the one aggregate number always present -- safe for a first-turn greeting, and the one
    question RULES lets the model answer without a live tool call. There is no per-city/
    per-department preview list here anymore; recon on the real system found it does not use live
    tool calls at all (it eagerly pre-fetches everything into one payload) and that pflege-board's
    own actual tool-calling (TASK-62 search_postings/list_clinics/get_posting) is already a step
    beyond that, not something to downgrade to match it -- so a candidate naming any specific
    city, department, region or clinic is answered by a real tool call (RULES: TOOLS), never by a
    precomputed guess here that could go stale or duplicate what a tool would say more precisely.
    shortlist[] (up to CLOSE_LIMIT distinct clinics) and matching_clinics_count still turn up here,
    deterministically, once qualification, EITHER city or department_pref (see
    _city_or_department_satisfied), housing, AND documents (_documents_satisfied) are all settled:
    naming the exact clinics a candidate's anonymized profile may reach is compliance-sensitive
    enough (never invent a clinic name) that it stays harness-computed, not left to the model's
    recall of an earlier tool result several turns back."""
    all_rows = B.jobs_for({})
    cities = B.known_cities()[:8]

    filters = {}
    if card.get("qualification_path") not in (None, "reject"):
        filters["role"] = "pflegefachkraft"
    if card.get("city"):
        filters["city"] = card["city"]
    if card.get("department_pref"):
        filters["department"] = card["department_pref"]
    rows = B.jobs_for(filters)
    clinic_names = {(r.get("clinic_name") or r.get("employer") or "").strip() for r in rows} - {""}

    shortlist = []
    ready_to_close = bool(card.get("qualification_ok") and _city_or_department_satisfied(card)
                          and card.get("housing_known") and _documents_satisfied(card))
    if ready_to_close:
        seen = set()
        for r in rows:
            name = (r.get("clinic_name") or r.get("employer") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            shortlist.append({"clinic": name, "city": (r.get("city") or r.get("clinic_town") or "").strip(),
                              "department": r.get("department_hint")})
            if len(shortlist) >= CLOSE_LIMIT:
                break

    return {"open_jobs": len(all_rows), "cities": cities, "matching_clinics_count": len(clinic_names),
            "shortlist": shortlist, "matches": list(shortlist)}


# Gate-priority order for requirement_scoreboard()'s next_objective (TASK-91): a single computed
# hint naming the ONE gate still open, so the model does not have to infer priority purely from
# THINK_ORDER prose -- modeled on the real reference implementation's own code-computed per-turn
# objective (recon: a plain rule tells its model that objective is "the ONLY placement question
# for this turn," state only, same "the model writes the wording" split pflege-board already
# uses elsewhere). Never shown to the candidate verbatim.
_OBJECTIVE_ORDER = (
    ("region", "clarify region (Bayern vs. another Bundesland)"),
    ("qualification", "clarify the qualification path (Urkunde/Defizitbescheid/Kenntnisprüfung)"),
    ("city_or_department", "narrow down a city or department preference"),
    ("housing", "ask how many people need housing"),
    ("documents", "ask for a photo/PDF of the CV and/or Urkunde (or Defizitbescheid) to confirm what was said"),
    ("handoff_consent", "run the close sequence: state the shortlist, then ask anonymized-send consent"),
)


def requirement_scoreboard(card):
    """State only, never a script (RULES: 'the requirement scoreboard is state only'). One of
    satisfied | open | blocked per gate, so the model can see what is left without being told
    what to ask next -- except next_objective (TASK-91), a single computed string naming the one
    gate still open in priority order, purely a hint the model may act on or override (a candidate
    answering something else first is still fine, per the CLOSE SEQUENCE/ONE FORWARD STEP rules)."""

    def _q():
        path = card.get("qualification_path")
        if path in ("urkunde", "defizit", "kenntnispruefung"):
            return "satisfied"
        if path == "reject":
            return "blocked"
        return "open"

    board = {
        "region": "satisfied" if card.get("region") else "open",
        "qualification": _q(),
        "city_or_department": "satisfied" if _city_or_department_satisfied(card) else "open",
        "housing": "satisfied" if card.get("housing_known") else "open",
        "documents": "satisfied" if _documents_satisfied(card) else "open",
        "handoff_consent": "satisfied" if card.get("anonymous_send_consent") else "open",
    }
    board["next_objective"] = next(
        (label for key, label in _OBJECTIVE_ORDER if board[key] == "open"),
        "nothing open -- respond naturally, no open placement item left")
    return board


# --- the Claude call -------------------------------------------------------------------------

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "bubbles": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
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


def _parse_reply_json(text):
    """Parse the model's reply text as the one JSON object it was told to return. Tried in order:
    the text as-is; with a markdown fence stripped; the substring between the first ``{`` and the
    last ``}`` (covers stray prose the model adds despite ``--tools ""`` -- e.g. narrating a tool
    attempt before its actual answer, seen even with every built-in tool disabled). Each step is
    normalization of a known formatting artifact, not a repair of malformed JSON: if none of them
    parse, this still raises rather than guessing at a shape."""
    stripped = _strip_fence(text)
    for candidate in (text, stripped):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(stripped[start:end + 1])
    raise json.JSONDecodeError("no JSON object found", stripped, 0)


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
    command/code-execution/WebFetch tools; ``--tools ""`` goes further and disables every
    *built-in* tool, including the file-reading ones ``--restricted`` leaves in place -- a
    conversational turn has no use for any of them, and without them there is nothing for the
    model to do but answer with them (observed without ``--tools ""``: a stray file-read attempt
    narrated as prose ahead of the JSON, breaking the parse below). ``--tools`` only ever governs
    that built-in set, though: ``--mcp-config``/``--strict-mcp-config``/``--allowedTools`` (see
    ``_mcp_config_path``, ``MCP_TOOL_NAMES``) separately load exactly the three read-only
    board-query tools Luna may call in ``app/wa/luna/tools_server.py`` -- the model can look something up mid-turn,
    it just still cannot read a file, run a command or fetch a URL. The user payload goes over
    stdin rather than as a positional argument, so a long, growing message never risks an
    argument-length limit and never shows up in a process listing.

    ``reply(system, user, session_id)`` -> ``(out, next_session_id)``. ``session_id=None`` means
    "first contact with this thread": ``--session-id <a uuid this module generates>`` starts a
    fresh, named session so it can be resumed later. Any other value means "continue this
    session": ``--resume <session_id>``. Either way the id to persist comes back as
    ``next_session_id`` (normally the same one that went in; if the CLI ever renames a session on
    resume, this is where that would surface).
    """

    def __init__(self, reply=None):
        self._reply = reply or self._live_reply

    def _live_reply(self, system_text, user_text, session_id):
        import subprocess

        fresh = session_id is None
        this_session_id = session_id or str(uuid.uuid4())
        session_flags = (["--session-id", this_session_id] if fresh else ["--resume", this_session_id])
        C.LUNA_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                [C.LUNA_CLAUDE_BIN, "-p", "--restricted", "--tools", "", "--output-format", "json",
                 "--model", C.LUNA_MODEL, "--effort", C.LUNA_EFFORT,
                 "--mcp-config", str(_mcp_config_path()), "--strict-mcp-config",
                 "--allowedTools", ",".join(MCP_TOOL_NAMES),
                 "--system-prompt", system_text, *session_flags],
                input=user_text, capture_output=True, text=True, timeout=C.LUNA_TIMEOUT_SEC,
                cwd=C.LUNA_SESSION_DIR,
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
        try:
            out = _parse_reply_json(result)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p's result text was not the expected JSON object: {exc}: {result[:300]!r}")
        return out, str(envelope.get("session_id") or this_session_id)

    def reply(self, system_text, user_text, session_id=None):
        out, next_session_id = self._reply(system_text, user_text, session_id)
        return _validate(out), next_session_id


def _user_payload(text, card, scoreboard, snapshot, button_id=None):
    """This turn's ground truth, not the conversation itself -- the resumed session already has
    every earlier turn. latest_inbound is what the candidate just wrote; the rest is state that
    can change independently of anything either side said (new postings, a code-enforced card
    correction from a prior turn), so it is resupplied fresh every time rather than trusted to
    the model's memory of an earlier turn. is_button_reply (TASK-80) tells the model whether THIS
    reply is an actual button tap or typed text -- it cannot otherwise tell the two apart from
    latest_inbound alone, since a button's own title ("Ja, gerne") reads just like free text. This
    is what lets the CLOSE SEQUENCE rule honestly distinguish "the candidate tapped Ja" (consent is
    now recorded, in code, see turn()) from "the candidate typed something that looks like yes"
    (not consent -- the model must ask them to tap one of the two buttons instead)."""
    return json.dumps({
        "latest_inbound": text,
        "is_button_reply": bool(button_id),
        "card": card,
        "requirement_scoreboard": scoreboard,
        "market_snapshot": snapshot,
    }, ensure_ascii=False, sort_keys=True)


def _check(bubbles):
    if not (1 <= len(bubbles) <= MAX_BUBBLES):
        raise AssertionError(f"{len(bubbles)} bubbles, the style rule allows 1-{MAX_BUBBLES}")
    for b in bubbles:
        if not str(b or "").strip():
            raise AssertionError("empty bubble")
    return [str(b).strip() for b in bubbles]


def turn(text, thread, button_id=None, client=None):
    """Same contract as app/wa/brain.py:turn() -- {bubbles, buttons, slots, asked, stopped,
    matches, action} -- so app/wa/api.py can call either brain without knowing which one it got.
    ``slots`` here holds the Luna card (a different shape from the deterministic brain's slots;
    app/wa/store.py persists whatever dict it is given), including ``_session_id`` -- the Claude
    Code session this thread is resumed from, invisible to everything except this module.
    ``asked`` is unused by this brain and passed through unchanged so the store's schema does not
    need to know which brain wrote a thread.
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
    user_text = _user_payload(text, card, scoreboard, snapshot, button_id)

    cl = client or Client()
    out, session_id = cl.reply(system_text, user_text, card.get("_session_id"))
    card["_session_id"] = session_id

    patch = dict(out.get("card_patch") or {})
    # Never trust the model's own claim of consent, even if an older session or prompt drift still
    # emits the field (OUTPUT_SCHEMA/OUTPUT_INSTRUCTION no longer describe it at all) -- only an
    # actual button tap, below, may set anonymous_send_consent.
    patch.pop("anonymous_send_consent", None)
    was_offered = bool(card.get("anonymous_send_offered"))
    was_ok = card.get("qualification_ok")
    card.update(patch)

    # TASK-80: anonymous_send_consent is never trusted from the model's own card_patch (already
    # stripped from OUTPUT_SCHEMA/OUTPUT_INSTRUCTION, but stripped here too in case an older
    # session or prompt drift still emits it) -- it is set ONLY by an actual tap on one of the two
    # CONSENT_BUTTONS attached below, exactly the "decided in code, not by the model" pattern this
    # module already uses for opt-out/reject/out-of-scope-region.
    if button_id == CONSENT_YES_ID and card.get("anonymous_send_offered"):
        card["anonymous_send_consent"] = True
    elif button_id == CONSENT_NO_ID and card.get("anonymous_send_offered"):
        card["anonymous_send_consent"] = False
    just_offered = bool(card.get("anonymous_send_offered")) and not was_offered

    raw_bubbles = out.get("bubbles") or []
    buttons = []
    if patch.get("qualification_ok") is False and was_ok is not False:
        # The gate the model must not rephrase (prompts.py module docstring, point 2). This one
        # gate overrides no_send too -- the very first decline must always be said out loud.
        bubbles = [P.REJECT_BODY_DE]
        action = "explain_not_placeable"
    elif out.get("no_send") or not raw_bubbles:
        # A legitimate "nothing new to say" turn (e.g. a duplicate reopen already answered, or a
        # closed exchange with only acknowledgements since) -- zero bubbles is correct here, not
        # a violation of the one-to-two-bubble style rule, which is about turns that DO speak.
        # Trusting the empty array on its own (not only the no_send flag) matters in practice:
        # a model that means to stay silent does not always also remember to set the flag, and an
        # empty bubbles list is unambiguous regardless of what the flag says.
        bubbles = []
        action = str(out.get("action") or "no_send")
    else:
        bubbles = _check(raw_bubbles)
        action = str(out.get("action") or "reply_now_conversational")
        if just_offered:
            # The turn where the model just asked for the anonymized send: attach real, tappable
            # buttons rather than leaving consent to however the candidate happens to phrase "yes".
            buttons = list(CONSENT_BUTTONS)

    if out.get("escalate_to_manager"):
        card["_escalated"] = True
        card["_escalate_reason"] = out.get("escalate_reason")

    return {"bubbles": bubbles, "buttons": buttons, "slots": card, "asked": asked, "stopped": False,
            "matches": snapshot.get("matches") or [], "action": action}
