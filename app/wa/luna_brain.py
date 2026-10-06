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
2. Whenever the reply explains not-placeable on a not-placeable card (the first turn, and a
   repeat the model chooses to answer), the reject bubble is the locked German
   text (prompts.REJECT_BODY_DE), not the model's own phrasing -- matching the source's
   "process-exact wording, the model must not rephrase" rule for this exact gate.
3. A named region outside Bavaria gets the locked out-of-scope bubble
   (prompts.OUT_OF_SCOPE_REGION_DE), because this board has no data for anywhere else and a
   model-authored answer could imply otherwise. Typed text on a thread without card.campaign or
   card.declined only (``_region_shortcut_applies``).

Everything else -- tone, which question to ask, how to phrase the market snapshot, when to
escalate -- is the model's call, per turn, from the state this module hands it.
"""
import json
import logging
import math
import os
import pathlib
import re
import sys
import time
import uuid

from .. import data as D
from . import brain as B
from . import config as C
from . import slots as SL
from . import store as ST
from .luna import board_vocabulary as BV
from .luna import closing_gate as CG
from .luna import escalation as ESC
from .luna import grounding as GR
from .luna import offer as OF
from .luna import prompts as P
from .luna import refusal as RF
from .luna import source_link as SRC
from .luna import tools_server as TS

log = logging.getLogger(__name__)

MAX_BUBBLES = 2

# Explicit button-confirmed consent (TASK-333): the real reference system has this same rigor for
# its own harder, named-clinic-submission gate (a tappable "Ja, ich bestätige"), never inferred
# from free text. This harness has no named-submission step, but applies the same discipline to
# the one consent point it does have -- anonymous_send_consent is set ONLY by a genuine tap on one
# of these two buttons (see turn(), below), never trusted from the model's own card_patch.
CONSENT_YES_ID = "consent:yes"
CONSENT_NO_ID = "consent:no"
CONSENT_BUTTONS = [{"id": CONSENT_YES_ID, "title": "Ja, gerne"}, {"id": CONSENT_NO_ID, "title": "Nein danke"}]

# card.documents[].reuse of a document imported from the candidate's earlier contact (TASK-342).
REUSE_PENDING, REUSE_CONFIRMED, REUSE_DECLINED = ST.REUSE_PENDING, ST.REUSE_CONFIRMED, ST.REUSE_DECLINED

_LUNA_DIR = pathlib.Path(__file__).resolve().parent / "luna"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
# TASK-162: constitution.json carries a "{client}" placeholder (this repo is public) wherever it used
# to name the client directly -- substituted here, once, at import time. Plain substring replace, same
# reasoning as prompts.py's own _CLIENT_NAME substitution: nothing else in this JSON's dumped text
# looks like "{client}" by accident.
_CONSTITUTION_TEXT = json.dumps(json.loads((_LUNA_DIR / "constitution.json").read_text(encoding="utf-8")),
                                ensure_ascii=False, indent=2).replace("{client}", C.client()["name"])
_QUALIFICATION_TEXT = json.dumps(json.loads((_LUNA_DIR / "qualification_knowledge.json").read_text(encoding="utf-8")),
                                 ensure_ascii=False, indent=2)

# The tools Luna herself may call, as the CLI names an external MCP tool
# (mcp__<server-name>__<tool-name> -- confirmed live, this is not documented anywhere formal).
# Three general board queries, four with the filter already preset for a common candidate need
# (TASK-213: a filter the model has to assemble out of bare parameter names goes unused -- the housing
# one did, for a whole task), and the two fallbacks for what the presets do not cover: this repo's own
# agent docs and an allowlist of public board GET paths. Every one of them is read-only.
# tools_server.py also defines get_clinic_contact (with its own tests) -- deliberately NOT in this
# tuple (TASK-424): contact details are for the human handoff after consent (app/wa/queue.py), never
# something the candidate-facing conversation itself should be able to surface.
# Model-visible (tool names are mcp__<server>__<tool>): a neutral word, never a brand the model could
# repeat to a candidate (TASK-203: asked who we are, Luna named the repo).
MCP_SERVER_NAME = "jobs"
MCP_TOOL_NAMES = tuple(f"mcp__{MCP_SERVER_NAME}__{t}" for t in
                       ("search_postings", "get_posting", "list_clinics",
                        "search_postings_with_housing", "list_clinics_with_housing",
                        "list_cities_with_postings", "count_postings",
                        # TASK-374: ranks the board against this thread's stored CV. Needs the thread's
                        # number to find that CV, which is why _mcp_config_path passes WA_LUNA_PHONE.
                        "match_cv_to_postings",
                        "read_board_docs", "board_api_get",
                        # TASK-360 round 7. Missing from this tuple until 2026-09-23 while
                        # prompts.py's SHOW_CLINIC_PHOTOS rule already told the model to call it:
                        # --allowedTools is an allowlist, so every call the model made was denied and
                        # the funnel's climax moment silently degraded to text.
                        "show_clinic_photos",
                        # TASK-290/291, Ivan 2026-09-24: paged DB history (replaces the removed
                        # look_at_phone live-screen read, TASK-289) and CV-edit-assist built on it.
                        "read_history", "read_document", "find_stored_cv", "send_updated_cv"))

#: How "this turn must not send" reaches the spawned tools server. show_clinic_photos is the one tool
#: that SENDS, and it sends by calling the phone rail itself -- so shadow_run's own "never call
#: WhatsApp send, regardless of WA_AUTOSEND" contract does not reach it, and a dry run would put real
#: photos in a real candidate's chat.
#:
#: READ FROM os.environ ONLY FOR A WHOLE-PROCESS DRY RUN (tools/wa_rehearse.py, which is its own
#: process and sets it before importing anything). An in-process caller asks per turn instead --
#: turn(..., no_send=True) -> Client.no_send -> _mcp_config_path. shadow_run.shadow_turn used to do
#: it by assigning os.environ["WA_LUNA_NO_SEND"] = "1", which never unset: one dry run anywhere in a
#: long-lived process (the uvicorn worker serving the webhook) silently turned every LATER real
#: turn's show_clinic_photos into a no-op that still reported success. The full test suite is where
#: this surfaced -- tests/test_wa_luna_shadow_run.py leaked the variable into every
#: tests/test_wa_luna_tools.py case that ran after it in the same process.
NO_SEND_ENV = "WA_LUNA_NO_SEND"


def _write_atomic(path, text):
    """Two turns can run at once (the webhook worker, catch-up, a campaign send); a half-written file
    read by a spawned server would be a parse error inside somebody else's turn."""
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _board_vocabulary_path():
    """Count the board's filter vocabulary HERE, in the process that already holds a warm snapshot, and
    write it next to the mcp config for the tools server to pick up (TASK-213 review).

    The tools server is spawned fresh for every single turn. Counting the vocabulary there meant a cold,
    synchronous Supabase build -- 8-17s measured -- between the CLI starting that process and the MCP
    handshake, under the CLI's 30s connect deadline (MCP_TIMEOUT), on every turn including the ones that
    never call a tool; and a board hiccup there raised, killed the server, and left this process none the
    wiser. This process refreshes its snapshot in the background and keeps serving the cached board while
    a refresh fails (app/data.py:refresh), so the lines are built from the same rows market_snapshot is
    built from in this very turn."""
    path = C.LUNA_SESSION_DIR / "board_vocabulary.json"
    _write_atomic(path, json.dumps(BV.vocabulary_lines(), ensure_ascii=False))
    return path


# The board keys D._snap actually carries (app/data.py:_snap) minus the state-machine fields
# (at/loading/error) that must not travel with it -- the tools server stamps its own `at` on load so a
# short-lived child never reads as stale, and `loading`/`error` are this process's own build state, not
# the child's. Same set tests/luna_fixture_tools_server.py already dumps for the same reason.
_BOARD_SNAPSHOT_KEYS = ("jobs", "clinics", "by_clinic", "facets", "taxonomy")


def _board_snapshot_path():
    """Hand the tools server the board rows themselves, not just the vocabulary counted off them
    (TASK-273): the tools server's own D._snap (data.py:186) starts empty in its fresh subprocess, so
    the first board tool call of every turn -- search_postings, list_clinics, count_postings,
    match_cv_to_postings, get_posting, anything hitting D.filter_jobs/D.filter_clinics -- used to hit
    D.snapshot()'s `if force or (empty and not loading): refresh()` and cold-build synchronously: the
    same 8-17s TASK-213 already moved the vocabulary count off this exact path for, just not the row
    data every other tool queries. This process already holds a warm snapshot (market_snapshot, called
    before _live_reply spawns the CLI) -- write it out the same way _board_vocabulary_path does, and
    the child primes its own D._snap from it (tools_server.py, mirroring
    tests/luna_fixture_tools_server.py's fixture-board priming) instead of building one from Supabase."""
    path = C.LUNA_SESSION_DIR / "board_snapshot.json"
    _write_atomic(path, json.dumps({k: D._snap[k] for k in _BOARD_SNAPSHOT_KEYS}, ensure_ascii=False))
    return path


def _mcp_config_path(ready_path, phone=None, no_send=False, role_class=None):
    """Write (once per turn) the --mcp-config file pointing the CLI at tools_server.py,
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
    same one on both sides of the subprocess boundary (see tools_server.py's own handling).

    WA_LUNA_BOARD_VOCABULARY carries the tool descriptions' vocabulary counted here
    (_board_vocabulary_path), WA_LUNA_BOARD_SNAPSHOT the board rows themselves (_board_snapshot_path,
    TASK-273) so the child's D._snap starts primed instead of cold-building on the first tool call,
    WA_LUNA_TOOLS_READY the file that server stamps once its tools are
    registered -- ``_live_reply`` raises when that stamp is missing after the run, because a tools
    server that never started is otherwise indistinguishable from a turn that just did not call one.

    WA_LUNA_ROLE_CLASS (TASK-302 point 6): this card's own known role class (_known_role_class), the
    same explicit per-turn env contract as WA_LUNA_PHONE just above -- count_postings reads it as its
    own default when the model calls it with role_class empty, so a plain count question is read
    against the role this candidate already settled on instead of quietly covering every role class at
    once. Empty when the card has none yet (qualification not settled) or turn() was called without a
    role at all (a unit test): the tool then falls back to whatever role_class the model itself passed
    (possibly none, exactly today's behaviour).

    WA_BRIDGE_URL/WA_BRIDGE_TOKEN/WA_AUTOSEND (TASK-360 round 7, Ivan 2026-09-23; WA_AUTOSEND added
    TASK-250): show_clinic_photos is the first tool in this server that SENDS something rather than
    only reading -- it calls app.wa.bridge.Client() itself, which needs the URL/token to reach the
    phone rail's executor, and now reads C.AUTOSEND itself to decide whether to send at all (the same
    gate api._send applies to every other outbound). Every other env value here is explicit rather
    than inherited (this dict, not the parent's os.environ, is the subprocess's whole environment) for
    the same reason WA_SQLITE_PATH is: a test's own bridge credentials -- and a test's own AUTOSEND
    flip -- must reach the subprocess the same deliberate way a test board does. Without this, the
    subprocess's own import of app.wa.config would see WA_AUTOSEND unset and read AUTOSEND as False
    always, which would not gate show_clinic_photos so much as permanently disable it.

    WA_REPLY_SCOPE/WA_META_SCOPE (Ivan, 2026-09-27): the send-scope kill switches ``transport.
    scope_refusal`` reads -- same explicit-not-inherited reason as WA_AUTOSEND just above, or the
    subprocess would see them unset and read the "all" default no matter what the parent has.

    One file per turn, named from ready_path's own uuid stem (TASK-249): the webhook worker, the
    3-minute catch-up poller and a campaign send are separate OS processes sharing this same
    LUNA_SESSION_DIR, so a FIXED filename here let one turn's WA_LUNA_PHONE overwrite another's
    between this write and the CLI opening the file -- show_clinic_photos then acted
    on the wrong candidate's number. _write_atomic only rules out a torn read of one file, not two
    turns racing over the same path.

    Unlike ready_path, nothing unlinks this file when the turn ends: the CLI only ever reads it
    once, at MCP handshake time, so a stale one left behind cannot be read into a later turn the
    way the fixed name could -- but tests/test_wa_luna_brain.py reads the config back through the
    subprocess.run mock's captured argv well after the call returns, so deleting it here would
    take that inspection point away. C.LUNA_SESSION_DIR/mcp_config therefore grows by one file
    (carrying a candidate's phone number) per turn with nothing purging it -- a real gap, but a
    separate finding from the clobber this fixes."""
    C.LUNA_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    config_dir = C.LUNA_SESSION_DIR / "mcp_config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / f"{ready_path.stem}.json"
    config = {"mcpServers": {MCP_SERVER_NAME: {"command": sys.executable,
                                                "args": ["-m", "app.wa.luna.tools_server"],
                                                "cwd": str(_REPO_ROOT),
                                                "env": {"PYTHONPATH": str(_REPO_ROOT),
                                                        "WA_SQLITE_PATH": str(C.SQLITE_PATH),
                                                        "WA_LUNA_SESSION_DIR": str(C.LUNA_SESSION_DIR),
                                                        "WA_LUNA_BOARD_VOCABULARY": str(_board_vocabulary_path()),
                                                        "WA_LUNA_BOARD_SNAPSHOT": str(_board_snapshot_path()),
                                                        "WA_LUNA_TOOLS_READY": str(ready_path),
                                                        # TASK-374: whose CV match_cv_to_postings reads.
                                                        # Empty when turn() was called without a thread
                                                        # row (a unit test): that tool then says so and
                                                        # the model answers without it.
                                                        "WA_LUNA_PHONE": str(phone or ""),
                                                        "WA_LUNA_ROLE_CLASS": str(role_class or ""),
                                                        "WA_BRIDGE_URL": C.BRIDGE_URL,
                                                        "WA_BRIDGE_TOKEN": C.BRIDGE_TOKEN,
                                                        "WA_AUTOSEND": "1" if C.AUTOSEND else "",
                                                        # Send-scope kill switches (Ivan, 2026-09-27):
                                                        # same reason WA_AUTOSEND is passed explicitly
                                                        # above -- without this the subprocess's own
                                                        # config import would see these unset and read
                                                        # the "all" default regardless of the parent's.
                                                        "WA_REPLY_SCOPE": C.REPLY_SCOPE,
                                                        "WA_META_SCOPE": C.META_SCOPE,
                                                        NO_SEND_ENV: "1" if no_send else os.environ.get(NO_SEND_ENV, "")}}}}
    _write_atomic(path, json.dumps(config))
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


_BAYERN_RE = re.compile(r"(?<![a-zäöü0-9-])(bayern|bavaria)(?![a-zäöü0-9-])", re.I)


def named_non_bavaria_land(text):
    """The out-of-scope Bundesland named in this message, or None. Whole-word (and not glued to
    a hyphen either), so 'Hessendorf' and 'NRW-Fan-Artikel' do not fire on 'Hessen'/'NRW'.

    None when Bayern is ALSO named in the same message (real bug, TASK-360 adjacent, 2026-09-22 --
    found by reading the full real candidate-history corpus): 'In Bayern oder in Baden-Württemberg'
    used to trip this shortcut on 'Baden-Württemberg' alone, sending the canned OUT_OF_SCOPE_REGION_DE
    text (which never even names the state that WAS just asked about) and silently setting
    card.region to the wrong Land, even though the candidate explicitly included Bayern -- the one
    region this board actually has. The REGION prompt rule (app/wa/luna/prompts.py) exists precisely
    for this mixed case and needs the model to actually run, not this shortcut intercepting first."""
    low = _fold(text)
    if _BAYERN_RE.search(low):
        return None
    for land in NON_BAVARIA_LAENDER:
        if re.search(r"(?<![a-zäöü0-9-])" + re.escape(land) + r"(?![a-zäöü0-9-])", low):
            return land
    return None


# --- market snapshot: structured facts for the model, never rendered text -------------------
# The model writes the sentence; this only supplies what is true right now, from the same
# filter app/wa/brain.py and GET /api/jobs use, so the harness and the API cannot drift.

# TASK-373: the close-sequence shortlist was the first place Ivan's "at most five" applied; it is now
# one cap for every place a result set is cut to what a candidate may be shown, and it lives in
# app/wa/luna/offer.py, next to the assembly it governs.
CLOSE_LIMIT = OF.OFFER_LIMIT


def _city_or_department_satisfied(card):
    """The one predicate for the 'city_or_department' gate -- shared by requirement_scoreboard()
    and market_snapshot() so they cannot silently disagree about what counts as answered (a real
    bug found live, TASK-417: market_snapshot's ready_to_close used to require BOTH city AND
    department_pref while requirement_scoreboard told the model this gate was satisfied by EITHER
    -- a candidate who is genuinely flexible on department (a real, valid answer, not a missing
    one) then saw requirement_scoreboard say 'satisfied' while market_snapshot never actually
    produced a shortlist to close with, stalling the conversation indefinitely)."""
    return bool(card.get("city") or card.get("department_pref"))


def housing_needed(card):
    """TASK-211: does this candidate need a flat? True/False as they answered it, None when the card does not say.

    ``card["housing_needed"]`` is the answer to the plain yes/no gate question; ``people_count`` is only ever
    recorded as "how many people would live in the flat" (the question is asked only after a yes, here and in the
    history import's own contract, docs/whatsapp.md), so a card that carries a headcount states a flat is wanted
    even when it predates this field. ``housing_known`` alone does NOT: it says the housing question was answered
    at some point (an imported card, app/wa/luna/import_history.py), never what the answer was -- guessing "needs
    one" from it would be inventing the fact this whole gate exists to know.

    The one criterion both the shortlist (market_snapshot) and the post-consent handoff
    (app/wa/queue.py:card_to_candidate) read, so they cannot promise different clinics."""
    if isinstance(card.get("housing_needed"), bool):
        return card["housing_needed"]
    if card.get("people_count"):
        return True
    return None


def _housing_satisfied(card):
    """The housing gate: a No settles it on its own, a Yes needs the headcount too (TASK-211).

    A card carrying only ``housing_known`` -- what import_history/migrate_candidates produce for an old-system
    candidate whose facts row says the topic was covered but not what was said -- is NOT settled (review
    2026-09-16). It used to be, and that closed the gate on the answer never existing: the yes/no was never
    asked, while the shortlist and the post-consent handoff both ran unfiltered (housing_needed None), i.e.
    the exact bug TASK-211 was filed for, for exactly the population the campaigns target. This is not
    re-asking an answered question either: the old system asked its own, coarser one and kept no answer we
    can read, so a single plain yes/no here is the first time this gate's question is put to them."""
    needed = housing_needed(card)
    if needed is True:
        return bool(card.get("people_count"))
    return needed is False


def housing_flexible(card):
    """TASK-211 review: "wanted a flat, a clinic without one is also an option" -- the answer to the HOUSING
    rule's follow-up when no matching clinic offers one. True/False as they answered it, None while they did
    not. It never rewrites housing_needed: without a field of its own the only way to record the Ja was
    flipping housing_needed to false, which erased the stated need from the human handoff (queue.py:
    ``needs_housing``) for a family that had explicitly asked for a flat. Read by market_snapshot and
    queue.build_queue_entry: a flexible candidate is matched against every posting again, a candidate who
    still needs a flat only against the ones the board marks with housing."""
    v = card.get("housing_flexible")
    return v if isinstance(v, bool) else None


def _counts_for_gate(doc):
    """TASK-342: a document imported from the candidate's earlier contact (app/wa/luna/import_history.py) counts
    only once the candidate confirmed it may be reused; everything received on WhatsApp counts as before."""
    return not doc.get("imported") or doc.get("reuse") == REUSE_CONFIRMED


def _cv_document_received(card):
    """TASK-427: a file classified as lebenslauf is in card["documents"] (app/wa/api.py:_ingest_media)."""
    return any(d["document_type"] == "lebenslauf" and _counts_for_gate(d) for d in card.get("documents", []))


def _is_qualification_document(doc, path):
    """TASK-427: urkunde path -> a non-helfer urkunde; defizit/kenntnispruefung path -> a non-helfer
    urkunde or a defizitbescheid; any other path (none yet, unknown, reject) -> nothing counts.
    'urkunde' is the German licence only: a home-country diploma is classified auslaendisches_diplom
    (app/cv.py:DOC_TYPES) and never counts, on any path."""
    if doc["document_type"] == "urkunde":
        return path in ("urkunde", "defizit", "kenntnispruefung") and doc["certificate_level"] != "helfer"
    if doc["document_type"] == "defizitbescheid":
        return path in ("defizit", "kenntnispruefung")
    return False


def _qualification_document_received(card):
    return any(_is_qualification_document(d, card.get("qualification_path")) and _counts_for_gate(d)
               for d in card.get("documents", []))


# TASK-375: the qualification_path values that SETTLE the gate, and the one that ends the funnel.
# A card sitting on one of the first three has passed the qualification stage; "reject" is a verdict
# that ends it loudly (turn() sends P.REJECT_BODY_DE -- TASK-380 made that true rather than assumed,
# see ``says_not_placeable`` below), and every other value -- "unknown", null, anything the schema
# lets through -- leaves it open.
QUALIFICATION_PATHS_SETTLED = ("urkunde", "defizit", "kenntnispruefung")
# Where a refused stage reset is recorded, so the choice is visible on the card rather than silent.
REFUSED_PATCH_KEY = "_refused_card_patch"


def keep_settled_qualification_path(card, was_path, at):
    """Refuse a card_patch that re-opens the qualification gate while the document that closed it is
    still physically on the card. -> the refusal record, or None.

    THE STAGE IS CODE-OWNED THROUGH THE SIDE DOOR TOO (audit F, 2026-09-21). Direct assertion of
    ``stage`` has been stripped since TASK-373, but one schema-legal ``card_patch
    {qualification_path: "unknown"}`` dropped a candidate from ``consent`` back to ``qualification``
    with the Urkunde still in card.documents -- and the next turn asked for a document it was holding.
    Five stages, through a field the prompt gave no guidance about.

    So the write is refused exactly where it contradicts the card's own evidence: the path was
    settled, the new value does not settle it, and a document that counted under the old path is
    still on the card. Everything else still goes through -- a correction from one real path to
    another (urkunde -> defizit), and any value at all while no qualification document has arrived,
    which is the normal case where the model is the only one who knows what the candidate said.

    "REJECT" IS NOT AN EXCEPTION TO THIS (TASK-380). It used to be waved through here on the grounds
    that it is a verdict rather than a reset -- but a card holding a classified German Urkunde is
    placeable by that document, so "reject" contradicts the card exactly as "unknown" does, and it
    does not merely re-ask for a document: it declares the candidate unplaceable and drops five
    funnel stages. The loud door stays open and is the one to use: ``qualification_ok: false``
    sends P.REJECT_BODY_DE, and a "reject" that no document contradicts now goes down that same
    door (see turn()).

    Scope, stated rather than widened: this is the gate a FILE on the card settles, so it is the one
    the card can contradict the model about. region, city and housing have no document behind them --
    clearing one of those is the model's reading of the conversation and stays the model's."""
    path = card.get("qualification_path")
    if was_path not in QUALIFICATION_PATHS_SETTLED or path == was_path:
        return None
    if path in QUALIFICATION_PATHS_SETTLED:
        return None
    if not any(_is_qualification_document(d, was_path) and _counts_for_gate(d)
               for d in card.get("documents", [])):
        return None
    card["qualification_path"] = was_path
    record = {"field": "qualification_path", "from": was_path, "to": path, "at": at,
              "why": "the qualification document for this path is on the card; the funnel stage is "
                     "not resettable through a side field"}
    card[REFUSED_PATCH_KEY] = [*(card.get(REFUSED_PATCH_KEY) or []), record]
    return record


def _reuse_pending(card, cv_in, qualification_in):
    """TASK-342: imported documents still waiting for the candidate's reuse answer that would settle a documents
    half still open -- a CV while no CV counts, a qualification document for the path while none counts."""
    path = card.get("qualification_path")
    return [d for d in card.get("documents", []) if d.get("imported") and d.get("reuse") == REUSE_PENDING and
            ((not cv_in and d["document_type"] == "lebenslauf") or
             (not qualification_in and _is_qualification_document(d, path)))]


def _documents_satisfied(card):
    """TASK-424, tightened by TASK-427 (Ivan's manual test 2026-09-13: a claimed Urkunde plus a sent
    Lebenslauf unlocked the close): BOTH a CV and the qualification document for the candidate's path
    have actually arrived and been classified -- a conversational 'ja, ich habe die Urkunde' never
    counts. Reads only card["documents"]: a legacy/migrated card with cv_text/urkunde_text but no
    documents list stays open (no fallback to the text keys), so the model asks for both again."""
    return _cv_document_received(card) and _qualification_document_received(card)


# One clinic identity for the shortlist, the offer and the grounding check (TASK-373): the board
# vocabulary's own, so the three can never disagree about what a clinic is called.
_clinic_name = OF.clinic_name
_clinic_names = OF.clinic_names


def _housing_cities(rows, wanted_city, home_bezirk):
    """TASK-211: the other cities whose postings the board marks with housing, under the same role/department
    filters -- [{city, regierungsbezirk, clinics}], the candidate's own Regierungsbezirk first, then by clinic
    count. Board rows only: this is what lets Luna answer "no flat in your city" with a real alternative instead
    of naming a town she made up, and it stays empty when the data has none.

    Own-Bezirk-first applies only when we actually know the candidate's Bezirk: comparing ``!= None`` sorted
    every city whose postings carry no regierungsbezirk to the FRONT, ahead of larger, genuinely nearby ones
    (review 2026-09-16), and the model reads this list top-down."""
    by_city = {}
    for r in rows:
        city, name = (r.get("city") or r.get("clinic_town") or "").strip(), _clinic_name(r)
        if not city or not name or city.casefold() == (wanted_city or "").casefold():
            continue
        entry = by_city.setdefault(city, {"regierungsbezirk": r.get("regierungsbezirk"), "clinics": set()})
        entry["clinics"].add(name)
    out = [{"city": city, "regierungsbezirk": e["regierungsbezirk"], "clinics": len(e["clinics"])}
           for city, e in by_city.items()]
    out.sort(key=lambda e: (0 if home_bezirk and e["regierungsbezirk"] == home_bezirk else 1,
                            -e["clinics"], e["city"]))
    return out[:CLOSE_LIMIT]


def market_snapshot(card):
    """-> {open_jobs, cities, matching_clinics_count, offer, shortlist, matches, department_filter, housing}.

    offer (TASK-373, Ivan 2026-09-21) is the whole answer to "never dump many vacancies at a candidate":
    at most offer.OFFER_LIMIT positions however large the matching set is, the count of how many more
    matched, the criteria that would actually narrow THIS set, and the two branches (narrow / pool) the
    same turn has to offer. shortlist is offer["positions"] -- one assembly, so the list the model names
    and the number it says are left over can never be counted from different rows. None until ready to
    close, exactly as shortlist has always been. department_filter
    (TASK-344) is ``slots.read_department_pref(card.department_pref)``, null without one: only status applied
    filters by department (any of its departments); ambiguous, flexible and unmatched build the shortlist from the
    other criteria. Deliberately thin
    (TASK-424, after reading how the real reference implementation actually works): open_jobs is
    the one aggregate number always present -- safe for a first-turn greeting, and the one
    question RULES lets the model answer without a live tool call. There is no per-city/
    per-department preview list here anymore; recon on the real system found it does not use live
    tool calls at all (it eagerly pre-fetches everything into one payload) and that pflege-board's
    own actual tool-calling (TASK-323 search_postings/list_clinics/get_posting) is already a step
    beyond that, not something to downgrade to match it -- so a candidate naming any specific
    city, department, region or clinic is answered by a real tool call (RULES: TOOLS), never by a
    precomputed guess here that could go stale or duplicate what a tool would say more precisely.
    shortlist[] (up to CLOSE_LIMIT distinct clinics) and matching_clinics_count still turn up here,
    deterministically, once qualification, EITHER city or department_pref (see
    _city_or_department_satisfied), housing, AND documents (_documents_satisfied: CV and qualification
    document both received, TASK-427) are all settled:
    naming the exact clinics a candidate's anonymized profile may reach is compliance-sensitive
    enough (never invent a clinic name) that it stays harness-computed, not left to the model's
    recall of an earlier tool result several turns back.

    housing (TASK-211): {needed, flexible, people_count, filtered, clinics_with_housing,
    clinics_ignoring_housing, city_regierungsbezirk, cities_with_housing}. Once ``housing_needed`` says a flat
    is wanted, the shortlist and matching_clinics_count come from the postings the board marks with housing
    (app/data.py:offers_housing, the ``housing=1`` filter GET /api/jobs takes) -- only 12 percent of them, so an
    unfiltered shortlist was a promise the board does not back. ``needed`` null means the yes/no was never
    answered (the gate is open then, so there is no shortlist either); ``flexible`` true is "wanted a flat, a
    clinic without one is also an option" and opens the search again while the need stays recorded; ``filtered``
    says which of the two the shortlist in this payload was built with. Both counts are reported, so Luna can
    say plainly that a city has open postings but none of them with a flat, and cities_with_housing -- filled
    only when the housing-filtered search found no flat at all -- gives the real alternatives from the same
    board instead of an invented one. Every shortlist entry carries its own ``housing`` flag, on every card:
    housing may only be asserted for a posting the board marks."""
    # TASK-302 point 6/f (review finding 4/5, 2026-09-25): the SAME role-class predicate build_warming
    # and count_postings' own WA_LUNA_ROLE_CLASS default already read (_known_role_class), not the
    # ad-hoc "qualification_path not in (None, 'reject')" this used to run inline -- which, unlike
    # _known_role_class, also treated card_patch's own "unknown" enum value as a settled role. One
    # predicate everywhere means open_jobs, matching_clinics_count and the tools can never disagree
    # about what this candidate is presumed to want.
    role = _known_role_class(card)
    all_rows = B.jobs_for({"role": role} if role else {})
    cities = B.known_cities()[:8]

    filters = {}
    if role:
        filters["role"] = role
    if card.get("city"):
        filters["city"] = card["city"]
    department_filter = None
    if card.get("department_pref"):
        # TASK-427 review: department_pref is the candidate's word ("Intensivstation"), the board filter an exact
        # match on its own vocabulary ("Intensiv/IMC"). TASK-344: a flexible word ("egal", "flexibel") or a word
        # the board has no department for filtered as written and emptied the shortlist (live, consent asked
        # with no clinic named); only board departments filter now, and department_filter says which. Review
        # 2026-09-15: 'Innere oder Intensiv' filtered on Intensiv alone and emptied the Augsburg shortlist.
        department_filter = SL.read_department_pref(card["department_pref"])
        if department_filter["status"] == "applied":
            filters["department"] = ",".join(department_filter["departments"])
    rows = B.jobs_for(filters)
    needed, flexible = housing_needed(card), housing_flexible(card)
    rows_with_housing = [r for r in rows if D.offers_housing(r)]
    # A candidate who needs a flat is matched against the postings that offer one, nothing else (TASK-211) --
    # until they say a clinic without one is also an option (housing_flexible), which opens the search again
    # without unsaying the need itself.
    filter_housing = needed is True and flexible is not True
    matching = rows_with_housing if filter_housing else rows

    # The wanted city's own Regierungsbezirk, from that city's own postings whatever role/department is
    # filtered -- what makes an alternative city a neighbour rather than the other end of Bavaria. Read off
    # the filtered rows it was empty in exactly the branch below that needs it (no matching posting in the
    # city at all, review 2026-09-16). None without a city on the card, and None for a city the board has no
    # posting in at all: a Bezirk is read off the board here, never guessed.
    city_bezirk = next((r.get("regierungsbezirk") for r in B.jobs_for({"city": filters["city"]})
                        if r.get("regierungsbezirk")), None) if filters.get("city") else None
    # Alternatives only when the filtered search itself found nothing: with a flat available where they asked,
    # naming other cities is noise the model could turn into a menu question (TASK-339).
    housing_cities = []
    if filter_housing and not rows_with_housing:
        wider = B.jobs_for({k: v for k, v in filters.items() if k != "city"}) if filters.get("city") else rows
        housing_cities = _housing_cities([r for r in wider if D.offers_housing(r)], card.get("city"), city_bezirk)

    ready_to_close = bool(card.get("qualification_ok") and _city_or_department_satisfied(card)
                          and _housing_satisfied(card) and _documents_satisfied(card))
    # TASK-373: the shortlist IS the offer's positions -- one assembly, one cap, one place the
    # remainder count and the narrowing criteria come from (app/wa/luna/offer.py:build_offer). Still
    # only once ready to close: TASK-424's rule that the payload carries no per-city preview stands,
    # and a five-position offer before the gates are settled would be exactly that preview.
    #
    # Which leaves offer null for most of the funnel, where the candidate is actually choosing --
    # so the VOLUME rule in prompts.py is written against BOTH sources, and mid-funnel it names the
    # listing tool's own {shown, total} rather than offer.* (TASK-375). Building the offer early
    # instead would have been the preview this comment refuses, so the prompt moved, not this.
    offer = OF.build_offer(matching) if ready_to_close else None
    shortlist = offer["positions"] if offer else []

    return {"open_jobs": len(all_rows), "cities": cities, "matching_clinics_count": len(_clinic_names(matching)),
            "offer": offer,
            "shortlist": shortlist, "matches": list(shortlist), "department_filter": department_filter,
            "housing": {"needed": needed, "flexible": flexible, "people_count": card.get("people_count"),
                        "filtered": filter_housing,
                        "clinics_with_housing": len(_clinic_names(rows_with_housing)),
                        "clinics_ignoring_housing": len(_clinic_names(rows)),
                        "city_regierungsbezirk": city_bezirk, "cities_with_housing": housing_cities}}


# Gate-priority order for requirement_scoreboard()'s next_objective (TASK-424): a single computed
# hint naming the ONE gate still open, so the model does not have to infer priority purely from
# THINK_ORDER prose -- modeled on the real reference implementation's own code-computed per-turn
# objective (recon: a plain rule tells its model that objective is "the ONLY placement question
# for this turn," state only, same "the model writes the wording" split pflege-board already
# uses elsewhere). Never shown to the candidate verbatim.
_OBJECTIVE_ORDER = (
    # TASK-339 review: every label names an open or a plain yes/no question, never options joined by "oder"
    # (live: "Gibt es eine Stadt ..., z. B. München ... oder Würzburg?" and "allein, oder ...?" got a bare Ja).
    ("region", "ask as a plain yes/no whether they are looking for a job in Bayern"),
    ("qualification", "clarify qualification: first a plain yes/no whether the German Urkunde is "   # TASK-339
                      "already in hand; only on no, the recognition step, again one yes/no at a time"),
    ("city_or_department", "ask which city in Bayern they want to work in, as an open question (a department "
                           "they name instead settles this too) -- no yes/no frame around a list of cities"),
    # TASK-211: the yes/no comes first; the headcount only after a yes (_HOUSING_HEADCOUNT_OBJECTIVE).
    ("housing", "ask ONE plain yes/no whether they need a flat (Unterkunft) at all -- no headcount in it yet"),
    ("documents", "ask for {missing} -- the close needs both the CV and the qualification document actually "   # TASK-427
                  "received, so name what is still missing again every turn until it arrives"),
    ("handoff_consent", "run the close sequence: state the shortlist, then ask anonymized-send consent"),
)

# TASK-427 review: a rejected qualification ends the checklist -- falling through to the next open gate told
# the model to ask a not-placeable candidate for documents every turn (prompts.py NOT PLACEABLE says stop).
_NOT_PLACEABLE_OBJECTIVE = ("not placeable -- no placement item open: no region, city, housing or document ask "
                            "(NOT PLACEABLE)")

# TASK-211: the second housing step, once the candidate said they do need a flat (card.housing_needed true).
_HOUSING_HEADCOUNT_OBJECTIVE = ("they need a flat: ask how many people would live in it, as an open question "
                                "(people_count) -- never as alone-or-with-family options")


def _qualification_document_name(path):
    """(name, note) for the qualification document the candidate's path needs, as next_objective says it
    (TASK-427)."""
    if path == "urkunde":
        return "the German Urkunde", "not a home-country diploma"
    if path in ("defizit", "kenntnispruefung"):
        return "the Defizitbescheid", "an already-issued German Fachkraft Urkunde counts too"
    return "the qualification document", "German Urkunde; Defizitbescheid on the recognition path"


def _missing_documents(card, cv_in, qualification_in):
    """The {missing} part of the documents objective: both by name, or the one still missing -- the
    photo/PDF hint on the document(s) being asked for, the already-received one last."""
    name, note = _qualification_document_name(card.get("qualification_path"))
    if not (cv_in or qualification_in):
        return f"BOTH the CV (Lebenslauf) AND {name} ({note}) as photos/PDFs, together in one ask"
    if qualification_in:
        return "the still-missing CV (Lebenslauf) as a photo/PDF (the qualification document is already in)"
    return f"the still-missing {name.removeprefix('the ')} as a photo/PDF ({note}; the CV is already in)"


_HELD_NAMES = {"lebenslauf": "the CV (Lebenslauf)", "urkunde": "the German Urkunde",
               "defizitbescheid": "the Defizitbescheid"}


def _reuse_objective(card, pending, cv_in, qualification_in):
    """TASK-342 documents objective while imported documents wait for the reuse answer: one yes/no naming what we
    hold, plus the document we do not hold (if any) by name."""
    name, note = _qualification_document_name(card.get("qualification_path"))
    held_cv = any(d["document_type"] == "lebenslauf" for d in pending)
    held_qualification = any(d["document_type"] != "lebenslauf" for d in pending)
    held = " AND ".join(dict.fromkeys(_HELD_NAMES[d["document_type"]] for d in pending))
    ids = ", ".join(str(d["id"]) for d in pending)
    label = (f"ask ONE plain yes/no whether we may use {held} they sent us earlier (card.documents ids {ids}, "
             f"imported, reuse pending), adding that they can simply send newer ones here instead; record the "
             f"answer in document_reuse (EARLIER DOCUMENTS)")
    still_missing = [n for n, missing in (("the CV (Lebenslauf)", not cv_in and not held_cv),
                                          (f"{name} ({note})", not qualification_in and not held_qualification))
                     if missing]
    if still_missing:
        label += f" -- we do not hold {' or '.join(still_missing)}: name it as still needed as a photo/PDF"
    return label


# --- the funnel stage (TASK-373) -------------------------------------------------------------
# Ivan, 2026-09-21: the card has to carry which stage the candidate is in, and the next turn has to
# resume from it instead of restarting. Not a second state machine: a stage is a NAME for the first
# gate requirement_scoreboard already found open, so the two cannot drift. The order is the harness's
# own gate order (region -> qualification -> where -> housing -> documents -> close), which is why
# "matching" (the criteria a match is made on) comes before the documents: the actual clinic choice
# only exists once market_snapshot has a shortlist, i.e. in the consent stage.
#
# The gates a requirement_scoreboard carries, as opposed to the computed hints sitting next to them
# (next_objective, stage, stage_since). app/wa/luna/reporting.py reads this instead of re-listing the
# keys it has to skip: TASK-373 added two hints, and its hard-coded skip list went stale on the spot.
SCOREBOARD_GATES = ("region", "qualification", "city_or_department", "housing", "cv_document",
                    "qualification_document", "documents", "handoff_consent")

_STAGE_GATES = (("region", "contact"), ("qualification", "qualification"),
                ("city_or_department", "matching"), ("housing", "matching"),
                ("cv_document", "cv"), ("qualification_document", "documents"),
                ("handoff_consent", "consent"))
FUNNEL_STAGES = tuple(dict.fromkeys([stage for _, stage in _STAGE_GATES] + ["submitted"]))


def funnel_stage(board):
    """The stage this candidate is in, from a requirement_scoreboard: the first gate that is not
    satisfied, under its funnel name. "submitted" once every gate including consent is satisfied --
    the point where app/wa/queue.py takes the thread to a human."""
    for gate, stage in _STAGE_GATES:
        if board[gate] != "satisfied":
            return stage
    return "submitted"


def requirement_scoreboard(card):
    """State only, never a script (RULES: 'the requirement scoreboard is state only'). One of
    satisfied | open | blocked per gate, so the model can see what is left without being told
    what to ask next -- except next_objective (TASK-424), a single computed string naming the one
    gate still open in priority order, purely a hint the model may act on or override (a candidate
    answering something else first is still fine, per the CLOSE SEQUENCE/ONE FORWARD STEP rules).

    TASK-427: cv_document and qualification_document are the two halves of documents (satisfied only
    when both are); next_objective names the missing one(s). A blocked qualification (reject) makes
    next_objective the not-placeable hint, whatever else is open. TASK-342: an imported document counts only once
    reuse is confirmed; while one that would settle an open half is pending, the documents objective is the reuse
    yes/no (_reuse_objective). TASK-211: housing is satisfied by a plain No on its own, or by a Yes plus the
    headcount (_housing_satisfied); between the two steps the objective is the headcount, never the yes/no
    again. This ``housing`` value, not card.housing_known, is the gate the prompt rules read: the flag follows
    the yes/no one step earlier, so between the Ja and the headcount the two disagree (review 2026-09-16).

    TASK-373: ``stage`` names the funnel stage these gates put the candidate in (funnel_stage), and
    ``stage_since`` when the card entered it -- null on the turn it changes. Together they are what the
    FUNNEL CONTINUITY rule resumes from."""

    def _q():
        path = card.get("qualification_path")
        if path in ("urkunde", "defizit", "kenntnispruefung"):
            return "satisfied"
        if path == "reject":
            return "blocked"
        return "open"

    cv_in, qualification_in = _cv_document_received(card), _qualification_document_received(card)
    board = {
        "region": "satisfied" if card.get("region") else "open",
        "qualification": _q(),
        "city_or_department": "satisfied" if _city_or_department_satisfied(card) else "open",
        "housing": "satisfied" if _housing_satisfied(card) else "open",
        "cv_document": "satisfied" if cv_in else "open",
        "qualification_document": "satisfied" if qualification_in else "open",
        "documents": "satisfied" if cv_in and qualification_in else "open",
        "handoff_consent": "satisfied" if card.get("anonymous_send_consent") else "open",
    }
    missing = _missing_documents(card, cv_in, qualification_in)
    pending = _reuse_pending(card, cv_in, qualification_in)

    def _objective(key, label):
        if key == "documents" and pending:
            return _reuse_objective(card, pending, cv_in, qualification_in)
        if key == "housing" and housing_needed(card) is True:
            return _HOUSING_HEADCOUNT_OBJECTIVE
        return label.format(missing=missing)

    if board["qualification"] == "blocked":
        board["next_objective"] = _NOT_PLACEABLE_OBJECTIVE
    else:
        board["next_objective"] = next(
            (_objective(key, label) for key, label in _OBJECTIVE_ORDER if board[key] == "open"),
            "nothing open -- respond naturally, no open placement item left")
    # TASK-373: the funnel stage, computed from the gates above so there is only ever one of them,
    # plus when the card entered it (turn() writes card["stage"]/["stage_at"] from this).
    board["stage"] = funnel_stage(board)
    board["stage_since"] = card.get("stage_at") if card.get("stage") == board["stage"] else None
    return board


# --- the warming turn (TASK-302, Ivan's "прогревающий ход") ----------------------------------
# Once per thread, the first turn after the candidate's primary interest AND their city are both
# established, the reply opens with a real vacancy instead of another bare question: one bubble
# naming it, one saying how many matched in total, one asking the next card-advancing question. This
# is the fourth thing decided in CODE rather than by the model's judgement, in the same spirit as the
# module docstring's three (opt-out, reject, out-of-scope region): WHICH posting is named, HOW MANY
# matched, and THAT it happens exactly once are all facts the harness computes from the live board and
# the card -- never the model's pick, never invented, never repeated. Only the German wording of the
# three bubbles stays the model's (prompts.py's WARMING rule).
WARMING_KEY = "_warming_sent_at"   # card: when the warming turn SUCCEEDED -- see build_warming/turn().
# TASK-302 fix pass (review finding 3 / Ivan's design point d, 2026-09-25): the id of the posting the
# succeeded warming turn actually named, next to WARMING_KEY -- visible proof of WHICH one, not only
# that one went out.
WARMING_PICK_KEY = "_warming_picked_posting_id"
# The posting ids the model has explicitly declined off a shown warming shortlist (warming_pick=null,
# a reason given) -- excluded from every later shortlist so a re-fire never puts the same declined set
# back in front of the model (design point d: "do not retry the same shortlist").
WARMING_DECLINED_KEY = "_warming_declined_postings"
# The latest non-success outcome, for a human to see: {"kind": "decline"|"failure"|"no_match",
# "reason": str, "at": iso} -- "no_match" also carries "criteria", the four facts build_warming
# searched on, so a later turn can tell whether anything actually changed (see build_warming).
WARMING_NOTE_KEY = "_warming_note"


def _known_role_class(card):
    """The one role class this card's own counts and searches should already be read against, once
    the qualification gate has settled on a real path. Originally factored out only for
    build_warming/count_postings; as of the TASK-302 fix pass (review finding 4/5, Ivan's design point
    f, 2026-09-25) this is now the SINGLE route every role-scoped number reads: market_snapshot's own
    ``matching``/open_jobs, build_warming's shortlist, and -- via the WA_LUNA_ROLE_CLASS env var
    _mcp_config_path threads through to the MCP subprocess -- tools_server._turn_role_class()'s
    default for search_postings, search_postings_with_housing, list_cities_with_postings,
    list_clinics_with_housing and count_postings alike (centralised in tools_server._job_filters, one
    seam, not five). This board's whole candidate-facing funnel is Pflegefachkraft
    (app/autopilot/matching.py:score defaults an unknown candidate role to the same constant) -- a
    settled qualification_path is a finding about THIS one role, never yet a fact about some other
    role class the candidate might also hold, so this is never anything but that one value or None
    (qualification not yet settled, or a reject -- QUALIFICATION_PATHS_SETTLED)."""
    return "pflegefachkraft" if card.get("qualification_path") in QUALIFICATION_PATHS_SETTLED else None


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (mean Earth radius 6371) -- TASK-302 point 4's radius widening has
    nothing else in this repo to reuse (no haversine/distance helper existed anywhere before this)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _warming_department_filter(card):
    """The board department string to hand _job_rows, or "" -- department_pref read the SAME way
    market_snapshot already reads it (SL.read_department_pref), never a second parsing of the
    candidate's word: only an "applied" reading filters, "unmatched"/"ambiguous"/"flexible" all widen
    to no department filter rather than raising out of a turn nobody asked to fail."""
    pref = card.get("department_pref")
    if not pref:
        return ""
    reading = SL.read_department_pref(pref)
    return ",".join(reading["departments"]) if reading["status"] == "applied" else ""


def _warming_filter_housing(card):
    """Same predicate as market_snapshot's own ``filter_housing``: wanted, and not flexible about it."""
    return housing_needed(card) is True and housing_flexible(card) is not True


def _warming_criteria_signature(card):
    """The four facts build_warming actually searches on. A no_match note only blocks a re-fire while
    this is unchanged (Ivan, TASK-302 point 4: 'fires again later only when those card criteria have
    changed') -- compared by equality, so ANY of role/city/department/housing moving re-opens it."""
    return {"role": _known_role_class(card), "city": (card.get("city") or "").strip(),
            "department_pref": card.get("department_pref") or None, "housing_needed": housing_needed(card)}


def _city_centre(city):
    """Mean (lat, lon) of this city's OWN board rows -- any role, department or housing, to maximise
    the chance of finding coordinates -- or None without at least one posting that carries both. Not
    the clinic registry: its columns (app/data.py's own comment on the clinics select) carry no
    lat/lon, so a city's own postings are the only coordinates this board has."""
    try:
        rows, _town = TS._job_rows(city=city)
    except TS.ToolError:
        return None
    points = [(r["lat"], r["lon"]) for r in rows if r.get("lat") is not None and r.get("lon") is not None]
    if not points:
        return None
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


def _warming_candidate(row, distance_km=None):
    """One shortlist entry as the model gets to see it (Ivan, TASK-302 second message point 2): id,
    clinic, city, department, title, the ad's FULL description, and -- unlike every other posting
    payload this harness ever hands the model (OF.position/tools_server._job_row) -- the original
    link. That is a deliberate, narrow exception to the "no link reaches the model" guarantee
    app/wa/luna/offer.py and app/wa/luna/source_link.py document elsewhere: it holds because the real
    enforcement is on the OUTPUT side (grounding.check_reply's LINK rule rejects a URL in any bubble
    regardless of what the model was handed), and Ivan asked for it explicitly, by name, for this one
    payload. description comes from app/data.py:job_detail (a live call; description is not in
    JOB_COLS, TASK-375) -- None when the board holds none, never invented."""
    posting_id = row.get("posting_id")
    description = (D.job_detail(posting_id) or {}).get("description")
    out = {"posting_id": posting_id, "clinic": OF.clinic_name(row), "city": D.town_of(row),
           "department": ", ".join(BV.departments_of(row)) or None, "title": row.get("title"),
           "description": description, "link": row.get("external_url") or "",
           "regierungsbezirk": row.get("regierungsbezirk"),
           "employment_types": list(row.get("employment_types") or []), "housing": D.offers_housing(row)}
    if distance_km is not None:
        out["distance_km"] = distance_km
    return out


def _warming_shortlist(rows, nearby, town=None, distances=None):
    distances = distances or {}
    candidates = [_warming_candidate(r, distances.get(r.get("posting_id"))) for r in rows[:10]]
    out = {"candidates": candidates, "matching_postings_total": len(rows), "nearby": nearby}
    if nearby:
        out["nearby_town"] = town
    return out


def _warming_pick_echo(card):
    """Fix pass (live 2026-09-25, second round): market_snapshot.warming only ever exists on the ONE
    turn build_warming fires, so a later "ist die Stelle noch frei" had nothing left to re-derive a
    title/description word (e.g. "Erwachsenenklinik") from -- a correct rejection under the old rule
    (that word truly was not evidence any more), fixed here by re-supplying it, not by loosening the
    check. Called on EVERY turn once card[WARMING_PICK_KEY] is set: re-fetches that ONE posting fresh,
    by id, through the SAME live-verified lookup tools_server.get_posting uses (TS.LIVE_BASE), never a
    cached copy of what the warming turn itself saw. None without a pick yet, and -- deliberately,
    without raising -- None the moment that posting is no longer live: a board state (verifier pulled
    it, ad expired), not an error; the candidate's own clinic/city/department are still fine to defend
    from GROUNDED_KEY alone, only the free-text title/description words need this per-turn refresh.
    Feeds grounding.turn_evidence's own candidates loop (never widens beyond this one posting)."""
    pick = card.get(WARMING_PICK_KEY)
    if not pick:
        return None
    row = next((j for j in D.filter_jobs(dict(TS.LIVE_BASE)) if j.get("posting_id") == pick), None)
    if row is None:
        return None
    return _warming_candidate(row)


def build_warming(card, scoreboard):
    """-> the one turn's warming payload, else None. Either {candidates, matching_postings_total,
    nearby, nearby_town?} (a real shortlist to pick from, up to 10) or {no_match: {criteria}} (nothing
    matched even within the radius -- a card-advancing question instead, see prompts.py WARMING).

    THE TRIGGER IS CODE'S, NEVER THE MODEL'S (Ivan, TASK-302 point 1). Every one of these has to hold:
    * region AND qualification are both already "satisfied" on ``scoreboard`` -- the closest this
      harness's own gates come to naming "primary interest established".
    * ``card["city"]`` itself is known -- Ivan's own wording, point 1; department_pref alone does not
      count (see _city_or_department_satisfied).
    * this thread has not already had its one SUCCESSFUL warming turn (``card[WARMING_KEY]`` -- review
      finding 3 / design point d: this is now written only on success, never before the model runs).
    * this exact search (_warming_criteria_signature) has not already come back empty this turn's
      criteria unchanged (``card[WARMING_NOTE_KEY]`` kind "no_match") -- design point 4: a no-match
      note only blocks a re-fire while role/city/department/housing stay what they were.

    Any one of those missing -> None: an ordinary turn, nothing invented, nothing recorded.

    THE SAME FUNCTIONS THE TOOLS USE (review finding 2). Built on tools_server._job_rows -- the
    resolved-town/department/role/housing filtering search_postings and count_postings already run,
    never app/wa/brain.py:jobs_for's own city= filter (which matches the posting's city OR its
    clinic's registry town, the Ansbach/Bruckberg bug documented at app/data.py:town_of). department
    is pre-resolved by _warming_department_filter exactly the way market_snapshot reads it, so the
    only ToolError _job_rows can still raise here is an unresolvable CITY word -- caught, and treated
    as no exact-city match rather than a crashed turn.

    ROWS ALREADY DECLINED THIS THREAD (card[WARMING_DECLINED_KEY]) are excluded from every candidate
    pool, exact-city and widened alike, so a re-fire after a decline never re-offers the same posting
    (design point d).

    NO MATCH IN THE CITY -> WIDEN BY RADIUS (Ivan, second message point 4). The SAME role/department/
    housing filters, without the city constraint, ranked by haversine distance from the mean lat/lon
    of the candidate's own city's postings (_city_centre), kept within config.LUNA_WARMING_RADIUS_KM
    (WA_LUNA_WARMING_RADIUS_KM, default 30 -- the knob IS the feature, not a safety cap). Still nothing
    within the radius, or no centre to measure from at all (an unresolvable city, or a city with no
    coordinates on any of its own rows) -> {no_match: {criteria}}."""
    if (card.get(WARMING_KEY) or scoreboard.get("region") != "satisfied"
            or scoreboard.get("qualification") != "satisfied" or not (card.get("city") or "").strip()):
        return None
    criteria = _warming_criteria_signature(card)
    note = card.get(WARMING_NOTE_KEY) or {}
    if note.get("kind") == "no_match" and note.get("criteria") == criteria:
        return None
    exclude = set(card.get(WARMING_DECLINED_KEY) or [])
    role, city = criteria["role"], criteria["city"]
    department, housing = _warming_department_filter(card), _warming_filter_housing(card)
    try:
        rows, _town = TS._job_rows(city=city, department=department, role_class=role or "", housing=housing)
    except TS.ToolError:
        rows = []
    rows = [r for r in rows if r.get("posting_id") not in exclude]
    if rows:
        return _warming_shortlist(rows, nearby=False)

    wide_rows, _town2 = TS._job_rows(department=department, role_class=role or "", housing=housing)
    wide_rows = [r for r in wide_rows if r.get("posting_id") not in exclude]
    centre = _city_centre(city)
    nearby = []
    if centre and wide_rows:
        for r in wide_rows:
            if r.get("lat") is None or r.get("lon") is None:
                continue
            d = _haversine_km(centre[0], centre[1], r["lat"], r["lon"])
            if d <= C.LUNA_WARMING_RADIUS_KM:
                nearby.append((d, r))
        nearby.sort(key=lambda pair: pair[0])
    if nearby:
        town = D.town_of(nearby[0][1])
        distances = {r.get("posting_id"): round(d, 1) for d, r in nearby}
        return _warming_shortlist([r for _, r in nearby], nearby=True, town=town, distances=distances)
    return {"no_match": {"criteria": criteria}}


# --- the Claude call -------------------------------------------------------------------------

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        # 3 only on the warming turn (TASK-302): this schema is documentation, never sent to the CLI
        # (nothing in this module passes OUTPUT_SCHEMA to the model) -- the real, per-turn enforcement
        # is _check/_checked_reply's own max_bubbles, which stays at MAX_BUBBLES (2) every other turn.
        "bubbles": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "rationale": {"type": "string"},
        "escalate_to_manager": {"type": "boolean"},
        # escalate_reason_code is the closed set (app/wa/luna/escalation.py:MODEL_CODES) -- the ONE
        # thing that decides whether escalate_to_manager is actually honoured. escalate_reason stays
        # free text: the human-readable detail next to whichever code applies, never a substitute
        # for it (a code outside MODEL_CODES makes the escalation a no-op -- see record_model_escalation).
        "escalate_reason_code": {"type": ["string", "null"], "enum": [*ESC.MODEL_CODES, None]},
        "escalate_reason": {"type": ["string", "null"]},
        "no_send": {"type": "boolean"},
        "next_ask": {"type": ["string", "null"]},
        # TASK-204: the model flags; the harness sends the fixed ack and owns card.declined (turn()).
        "decline": {"type": "boolean"},
        "decline_reason": {"type": ["string", "null"]},
        "re_engaged": {"type": "boolean"},
        # TASK-302 fix pass (Ivan's design point 2/d, 2026-09-25): present only on the turn
        # market_snapshot.warming.candidates is shown. warming_pick is the posting_id of the ONE
        # candidate that fits this card best (code only checks it is one of the ids shown -- BOARD
        # DATA IS TRUTH, the model's own judgement of fit is not second-guessed), or null when none of
        # the shown candidates genuinely fit and warming_why then carries that reason instead (a
        # decline -- turn() records it and never shows this exact shortlist again). warming_why is the
        # model's own short reason either way -- why this one fits, or why none did.
        "warming_pick": {"type": ["integer", "null"]},
        "warming_why": {"type": ["string", "null"]},
        # TASK-342: the candidate's answer on imported documents (card.documents ids); the harness records it.
        "document_reuse": {
            "type": "object",
            "properties": {"confirmed_ids": {"type": "array", "items": {"type": "integer"}},
                           "declined_ids": {"type": "array", "items": {"type": "integer"}}},
            "additionalProperties": False,
        },
        "card_patch": {
            "type": "object",
            "properties": {
                "already_placed": {"type": "boolean"},          # TASK-203
                "open_to_new_position": {"type": "boolean"},
                "region": {"type": "string"},
                "city": {"type": "string"},
                "department_pref": {"type": "string"},
                "role_verdict": {"type": "string", "enum": ["accept", "reject", "unclear"]},
                "qualification_ok": {"type": "boolean"},
                "qualification_path": {"type": "string",
                                       "enum": ["urkunde", "defizit", "kenntnispruefung", "reject", "unknown"]},
                "urkunde_status": {"type": "string"},
                # TASK-211: the yes/no answer itself. housing_known (the flag that it was answered) is the
                # harness's own, derived from this in turn() -- see CODE_OWNED_CARD_KEYS.
                "housing_needed": {"type": "boolean"},
                # TASK-211 review: "a clinic without a flat is also an option" -- the answer to the HOUSING
                # follow-up when nothing in their city offers one. It stands NEXT TO housing_needed; the need
                # itself is never unsaid, so the human handoff still reads "wanted a flat, accepts without".
                "housing_flexible": {"type": "boolean"},
                "people_count": {"type": "integer"},
                # TASK-373: which of the two branches the candidate picked after the offer -- narrow the
                # search, or go into the general pool and be put forward to every matching clinic. The
                # narrowing itself still lands in city/department_pref/housing_* as usual; this records
                # WHICH way they chose, so the human taking the thread over sees it.
                "match_branch": {"type": "string", "enum": [OF.BRANCH_NARROW, OF.BRANCH_POOL]},
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


# TASK-239: the CLI's own transcript store outlives neither a disk cleanup nor a config-dir move, and a
# --resume of a session it no longer has fails with this exact stderr text (checked against the live CLI,
# 2.1.270) -- distinguished from every other non-zero exit (auth, network, a real bug) so only THIS one
# is ever treated as recoverable by turn(), below.
class SessionNotFound(RuntimeError):
    def __init__(self, stale_session_id):
        self.stale_session_id = stale_session_id
        super().__init__(f"no transcript left for session {stale_session_id}")


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
    ``_mcp_config_path``, ``MCP_TOOL_NAMES``) separately load exactly the read-only board tools Luna
    may call in ``app/wa/luna/tools_server.py`` -- the model can look something up mid-turn, and since
    TASK-213 it can also read this repo's own board docs and one allowlisted public board API path
    through that same server; it just still cannot read a file, run a command or fetch a URL (the docs
    tool serves four fixed documents, the API tool an allowlist of GET paths, and neither reaches
    anything but the public board). The user payload goes over
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
        # TASK-374: the thread's own number, set by turn() on the client it is about to use, and passed
        # to the tools server so match_cv_to_postings can read this candidate's stored CV. An attribute
        # rather than a constructor argument because tests stand a client up as ``LB.Client()`` with no
        # arguments (tests/test_wa_test_threads.py and five others), and a turn must keep working when
        # nobody set it -- the tool then says it has no number and the model answers without it.
        self.phone = None
        # TASK-302 point 6: this card's own known role class (_known_role_class), set by turn() the
        # same way self.phone is -- count_postings reads it as its own default. Same reason as
        # self.phone: a turn must keep working when nobody set it (the tool then reads whatever
        # role_class, if any, the model itself passed).
        self.role_class = None
        # Per-turn dry run, set by turn(no_send=True) on the client it is about to use -- same idiom
        # and same reason as self.phone above. NOT an environment variable: see NO_SEND_ENV.
        self.no_send = False

    def _live_reply(self, system_text, user_text, session_id):
        import subprocess

        fresh = session_id is None
        this_session_id = session_id or str(uuid.uuid4())
        session_flags = (["--session-id", this_session_id] if fresh else ["--resume", this_session_id])
        C.LUNA_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        # Stamped by the tools server once its tools are registered; one file per turn, so two turns
        # running at once cannot read each other's.
        ready_path = C.LUNA_SESSION_DIR / "tools_ready" / f"{uuid.uuid4()}.json"
        spawned_at = time.time()
        tools_ready_at = None
        try:
            try:
                proc = subprocess.run(
                    [C.LUNA_CLAUDE_BIN, "-p", "--restricted", "--tools", "", "--output-format", "json",
                     "--model", C.LUNA_MODEL, "--effort", C.LUNA_EFFORT,
                     "--mcp-config", str(_mcp_config_path(ready_path, self.phone, self.no_send, self.role_class)),
                     "--strict-mcp-config",
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
                stderr = proc.stderr.strip()
                if not fresh and "No conversation found with session ID" in stderr:
                    raise SessionNotFound(this_session_id)
                raise RuntimeError(f"claude -p exited {proc.returncode}: {stderr[:500]}")
            # A board tool server that died at start, or that the CLI dropped for missing its connect
            # deadline, leaves no trace anywhere else: claude -p exits 0, is_error is false, stderr is
            # empty, the result envelope carries no MCP status (probed, CLI 2.1.270), and the turn
            # answers about the board with no board under it -- worse than any error, because it reads
            # like a checked answer. Same loud failure as a missing CLI: nothing is sent, the pending row
            # keeps the error and catch-up retries (TASK-341).
            if not ready_path.exists():
                raise RuntimeError("the board tools server never started for this turn (no readiness stamp "
                                   f"at {ready_path}): claude -p ran without {len(MCP_TOOL_NAMES)} board "
                                   f"tools the system prompt says are mandatory, so nothing it said about "
                                   f"the board was looked up. Run "
                                   f"`{sys.executable} -m app.wa.luna.tools_server` to see why it failed.")
            # TASK-287 (latency profiling): the stamp already carries the moment the CLI finished the
            # MCP handshake; read it before it's deleted so a turn's wall time can be split into
            # "CLI cold-start + board tools ready" vs. "everything after," without guessing.
            try:
                tools_ready_at = json.loads(ready_path.read_text(encoding="utf-8")).get("at")
            except (OSError, json.JSONDecodeError):
                pass
        finally:
            ready_path.unlink(missing_ok=True)
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p did not return JSON on stdout: {exc}: {proc.stdout[:300]!r}")
        if envelope.get("is_error"):
            raise RuntimeError(f"claude -p reported an error: {envelope.get('result')!r}")
        # TASK-287 (latency profiling): claude -p already computes this split (CLI wall time vs. the
        # model API's own time) on every call -- log it instead of throwing it away, so a slow turn is
        # attributable without adding new instrumentation later. warning, not info: this process sets
        # up no root logging config (same reason router.py/refusal.py/bridge_api.py only ever log at
        # warning/error), so an info call here would be silently dropped rather than reach journalctl.
        finished_at = time.time()
        log.warning(
            "luna turn timing: fresh=%s total_wall_ms=%d tools_ready_ms=%s cli_duration_ms=%s "
            "api_duration_ms=%s num_turns=%s",
            fresh, round((finished_at - spawned_at) * 1000),
            round((tools_ready_at - spawned_at) * 1000) if tools_ready_at else None,
            envelope.get("duration_ms"), envelope.get("duration_api_ms"), envelope.get("num_turns"),
        )
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


# --- what reached the candidate outside this session (TASK-203) ---------------------------------------
# The session only knows what the model itself wrote. Everything else sent to the number since its last
# turn -- a campaign template, a follow-up nudge, the decline ack, a locked reject/out-of-scope text, a
# reopen template sent instead of its bubbles, a manual send -- goes into the next payload, so a "Ja" is
# read against the message the candidate actually saw last.

LAST_TURN_KEY = "_luna_last_turn"   # card: {at, seen_through_id, own_message_ids}, written by api.process_owed_turn
# TASK-373: the clinic names this thread has already had in real evidence (a tool result or the
# harness offer). A later turn may name one of them again without re-searching -- see
# app/wa/luna/grounding.py, WHAT COUNTS AS EVIDENCE.
GROUNDED_KEY = "_grounded_clinics"
# TASK-380: the posting ids those names were grounded ON. Ivan's rule (a) is about the opening, and
# a memory of clinic names alone let "die Stelle in Onkologie ist noch frei" out after the verifier
# had removed every Onkologie posting the house had -- the house still had 32 others.
GROUNDED_POSTINGS_KEY = "_grounded_postings"
# TASK-379: what the last message on a TEST thread named, as [{clinic, posting_id, url}], so the
# footnote's offer can be honoured when the candidate takes it up. Written only for a thread
# wa_threads.is_test marks; a production card never has this key (app/wa/luna/source_link.py).
TEST_SOURCES_KEY = "_test_sources"
# Card keys only code writes; stripped from the model's card_patch.
CODE_OWNED_CARD_KEYS = ("anonymous_send_consent", "declined", "declined_reason", "declined_at", "re_engaged_at",
                        "campaign", LAST_TURN_KEY, "_session_id", "_unread_media",
                        # TASK-395 (Pro API contract): the first-escalation timestamp
                        # (app/wa/luna/escalation.py:record_escalation) -- stamped once by code, never
                        # something a card_patch may set or clear.
                        "_escalated_at",
                        # TASK-342: the documents gate list (reuse state included) and the imported history
                        "documents", "prior_contact", "prior_placement",
                        # TASK-208: opt-outs, declines and chat Stopps the earlier system recorded
                        "prior_opt_outs",
                        # TASK-373: the funnel stage and when it was entered are computed from the gates
                        # (funnel_stage), the grounded-clinic memory is written from real tool evidence,
                        # and match_branch_at timestamps the model's own match_branch -- none of the four
                        # is a fact the model may assert about itself.
                        "stage", "stage_at", "match_branch_at", GROUNDED_KEY, GROUNDED_POSTINGS_KEY,
                        # TASK-375/379: the record of a card_patch this harness refused, and the
                        # test-thread evidence links -- both are what CODE decided, not a finding
                        # the model may assert about itself.
                        REFUSED_PATCH_KEY, TEST_SOURCES_KEY,
                        # TASK-211: the housing gate's own flag, derived from housing_needed in turn(). The model
                        # writes the answer (housing_needed), never the flag -- a turn that set housing_known
                        # alone used to close the gate without the fact the shortlist filters on.
                        "housing_known",
                        # TASK-302: whether the warming turn already SUCCEEDED, which posting it named, which
                        # postings it has already been declined on, and the latest visible note on any other
                        # outcome are all CODE's own record (build_warming decides the shortlist, turn() judges
                        # what happened to it) -- never something the model may claim or clear itself, or a
                        # forged/omitted card_patch value would spend or repeat the thread's one warming turn.
                        WARMING_KEY, WARMING_PICK_KEY, WARMING_DECLINED_KEY, WARMING_NOTE_KEY)
# Outbound meta.action values no model turn writes; only used to find the last model-turn row on a card from
# before LAST_TURN_KEY existed.
NOT_MODEL_ACTIONS = ("followup", "media_ack", "decline_ack", "campaign")


def _legacy_seen_through(c, phone):
    """A session started before LAST_TURN_KEY: the id of the last outbound text/buttons/draft row a turn
    wrote (meta.action not in NOT_MODEL_ACTIONS), 0 when there is none."""
    rows = [r for r in ST.messages_for(c, phone, direction="out")
            if r["kind"] in ("text", "buttons", "draft") and r["meta"].get("action") not in NOT_MODEL_ACTIONS]
    return rows[-1]["id"] if rows else 0


# The operator inbox's own two messages (app/wa/api.py, app/wa/luna/agent_notes.py). They go out on
# the same thread the operator tests the funnel from, but they are the harness talking to a colleague
# about a work item, not anything the candidate persona said -- so they must not appear as "what we
# last told this candidate". Live, 2026-09-24: without this the ack "[агент] Принято, заметка #1"
# becomes last_outbound, and the operator's next German test turn is read as ANSWERING it (prompts.py
# THINK_ORDER step 4) and handed to the refusal classifier as our_last_message.
AGENT_NOTE_ACTIONS = ("agent_note_ack", "agent_note_done")


def _is_agent_note_message(row):
    return (row["meta"] or {}).get("action") in AGENT_NOTE_ACTIONS


def _delivery(c, wamid):
    """The latest Meta delivery status of an outbound message (wa_message_statuses), or None without one."""
    latest = ST.latest_message_status(c, wamid) if wamid else None
    if latest is None:
        return None
    return {"status": latest["status"], "error_codes": [e.get("code") for e in latest["errors"] or []]}


def _message_view(row, delivery=None):
    return {"wamid": row["wamid"], "kind": row["kind"], "text": row["body"], "at": row["at"],
            "action": row["meta"].get("action"), "template": row["meta"].get("template"), "delivery": delivery}


# The old bot's "already greeted" check (apps/connectors/candidate_reply_council.py _has_valentina_freeform_greeting):
# a free-form outbound message that names Valentina or the client, or opens with 'Hallo Frau/Herr'. Templates
# excluded. The client half is built from config/wa-client.json's name (TASK-162), same re.escape/case-
# insensitivity/alternation shape the old literal company-name branch always had.
_GREETING_RE = re.compile(r"ich bin valentina|" + re.escape(C.client()["name"].lower()), re.I)
_SALUTATION_RE = re.compile(r"^hallo\s+(frau|herr)\b", re.I)
_FREEFORM_KINDS = ("text", "buttons", "draft")


def introduced(c, phone):
    """True once a free-form message to this number introduced Valentina like the old bot checks it. Replaces
    fresh_session for the CAMPAIGN self-introduction (review 2026-09-14: the decline turn started a session whose
    bubbles were replaced by the fixed ack, so a re-engaged candidate never learned who writes)."""
    return any(_GREETING_RE.search(r["body"] or "") or _SALUTATION_RE.search(r["body"] or "")
               for r in ST.messages_for(c, phone, direction="out") if r["kind"] in _FREEFORM_KINDS)


def turn_context(c, t, turn_key):
    """-> {outbound_since_last_turn, last_turn_at, reply_context, seen_through_id, last_outbound} for the
    Luna turn answering inbound ``turn_key``. The caller (api.process_owed_turn, shadow_run) puts it on the
    thread as ``turn_context``; turn() moves it into the payload.

    outbound_since_last_turn: outbound rows after the card's LAST_TURN_KEY.seen_through_id that are not in
    its own_message_ids, oldest first, each with its latest ``delivery`` status; without a marker, every outbound
    row when there is no session yet (a campaign-opened thread sees its template), else rows after
    ``_legacy_seen_through``. A row whose latest status is failed is left out, and ``campaign_delivery_failed``
    makes turn() leave card.campaign out of the payload. ``introduced``: see ``introduced()``.
    reply_context: the inbound's kind, whether it is a template quick-reply tap and its payload, when it
    arrived, and the stored message it replies to (context.id) or ``found: false`` for one we do not hold.
    voice_note (TASK-210): the inbound message carries a transcript (api._transcribe_voice_note), which the caller
    passes to turn() as the text. Raises when ``turn_key`` is not a stored inbound message.

    last_outbound (TASK-386): the single most recent outbound row of ANY kind (campaign template, a
    model's own bubble, the decline ack, a locked reply, an admin send) strictly before this inbound
    arrived (``id < inbound["id"]``), as one ``_message_view``, or None with no prior outbound at all.
    Deliberately not filtered by the LAST_TURN_KEY marker the way outbound_since_last_turn is: the refusal
    classifier (app/wa/luna/refusal.py) needs the literal last thing the candidate actually read, whoever
    or whatever sent it, not only the events the model itself would not already remember from its own
    resumed session."""
    phone, card = t["phone"], t.get("slots") or {}
    inbound = ST.message_by_wamid(c, turn_key)
    if inbound is None or inbound["direction"] != "in" or inbound["phone"] != phone:
        raise RuntimeError(f"turn_context: {turn_key!r} is not a stored inbound message of {phone}")
    prior_outbound = [r for r in ST.messages_for(c, phone, direction="out")
                      if r["id"] < inbound["id"] and not _is_agent_note_message(r)]
    last_outbound = (_message_view(prior_outbound[-1], _delivery(c, prior_outbound[-1]["wamid"]))
                     if prior_outbound else None)
    marker = card.get(LAST_TURN_KEY)
    if marker:
        after_id, own = marker["seen_through_id"], set(marker["own_message_ids"])
    else:
        after_id, own = (_legacy_seen_through(c, phone) if card.get("_session_id") else 0), set()
    # A message Meta reported undelivered (latest status failed) never reached the candidate: not in the payload
    # (review 2026-09-14: a template failed with 131049, weeks later a spontaneous message was read as its answer).
    outbound = [view for view in (_message_view(r, _delivery(c, r["wamid"]))
                                  for r in ST.messages_for(c, phone, after_id=after_id, direction="out")
                                  if r["id"] not in own and not _is_agent_note_message(r))
                if (view["delivery"] or {}).get("status") != "failed"]
    campaign = card.get("campaign") or {}
    campaign_delivery_failed = bool(campaign) and (_delivery(c, campaign.get("wamid")) or {}).get("status") == "failed"
    meta = inbound["meta"]
    replies_to = None
    if meta.get("reply_to_wamid"):
        target = ST.message_by_wamid(c, meta["reply_to_wamid"])
        replies_to = ({"wamid": meta["reply_to_wamid"], "found": False} if target is None else
                      {**_message_view(target, _delivery(c, target["wamid"]) if target["direction"] == "out" else None),
                       "direction": target["direction"], "found": True})
    from .api import TEMPLATE_BUTTON_PREFIX
    button_id = meta.get("button_id") or ""
    is_template_button = inbound["kind"] == "button"
    reply_context = {"kind": inbound["kind"], "received_at": inbound["at"], "is_template_button": is_template_button,
                     "template_button_payload": (button_id.removeprefix(TEMPLATE_BUTTON_PREFIX) or None)
                     if is_template_button else None,
                     "replies_to": replies_to}
    return {"outbound_since_last_turn": outbound, "last_turn_at": (marker or {}).get("at"),
            "reply_context": reply_context, "introduced": introduced(c, phone), "voice_note": "transcript" in meta,
            "campaign_delivery_failed": campaign_delivery_failed, "seen_through_id": ST.last_message_id(c, phone),
            "last_outbound": last_outbound}


def turn_marker(c, phone, luna_turn, action):
    """The LAST_TURN_KEY value after a model turn was sent: its own rows are the outbound rows after
    seen_through_id whose body is one of the model's bubbles and whose meta.action is the turn's action
    (a locked text, the decline ack or a reopen template sent instead is not the model's and shows up in
    the next payload)."""
    bubbles = set(luna_turn["model_bubbles"])
    own = [r["id"] for r in ST.messages_for(c, phone, after_id=luna_turn["seen_through_id"], direction="out")
           if r["body"] in bubbles and r["meta"].get("action") == action]
    return {"at": luna_turn["at"], "seen_through_id": luna_turn["seen_through_id"], "own_message_ids": own}


def _user_payload(text, card, scoreboard, snapshot, button_id=None, documents_just_received=(), context=None):
    """This turn's ground truth, not the conversation itself -- the resumed session already has
    every earlier turn. latest_inbound is what the candidate just wrote; the rest is state that
    can change independently of anything either side said (new postings, a code-enforced card
    correction from a prior turn), so it is resupplied fresh every time rather than trusted to
    the model's memory of an earlier turn. is_button_reply (TASK-333) tells the model whether THIS
    reply is an actual button tap or typed text -- it cannot otherwise tell the two apart from
    latest_inbound alone, since a button's own title ("Ja, gerne") reads just like free text. This
    is what lets the CLOSE SEQUENCE rule honestly distinguish "the candidate tapped Ja" (consent is
    now recorded, in code, see turn()) from "the candidate typed something that looks like yes"
    (not consent -- the model must ask them to tap one of the two buttons instead).
    documents_just_received (TASK-427) lists the files that arrived since the last reply
    ({id, document_type, certificate_level}, empty on every other turn): a media message has an empty
    latest_inbound, and card.documents alone does not say which entry is new -- this does, wrong
    document types included.
    TASK-203: outbound_since_last_turn, last_turn_at, reply_context and introduced come from ``turn_context`` ([],
    None, None, None when the caller supplied none, e.g. a test calling turn() directly); fresh_session is true when
    no session exists yet, so the model has no memory of any earlier Valentina message. The card goes without
    LAST_TURN_KEY (bookkeeping, not a fact), and without campaign when Meta reported that template undelivered.
    voice_note (TASK-210): latest_inbound is the transcript of a voice note the candidate sent (prompts VOICE NOTE)."""
    from .api import TEMPLATE_BUTTON_PREFIX
    context = context or {}
    # TEST_SOURCES_KEY holds full ad URLs (TASK-379). It is hidden from the payload for the same
    # reason offer.py strips the board's links: the model must never see one, on any thread, or the
    # guarantee is a rule it could forget rather than a field it never reads (TASK-380).
    hidden = {LAST_TURN_KEY, TEST_SOURCES_KEY}
    hidden |= {"campaign"} if context.get("campaign_delivery_failed") else set()
    return json.dumps({
        "latest_inbound": text,
        # A template quick-reply tap is reply_context.is_template_button, never a consent-style button reply.
        "is_button_reply": bool(button_id) and not button_id.startswith(TEMPLATE_BUTTON_PREFIX),
        "reply_context": context.get("reply_context"),
        "outbound_since_last_turn": context.get("outbound_since_last_turn", []),
        "last_turn_at": context.get("last_turn_at"),
        "fresh_session": card.get("_session_id") is None,
        "introduced": context.get("introduced"),
        "voice_note": bool(context.get("voice_note")),
        "documents_just_received": list(documents_just_received),
        "card": {k: v for k, v in card.items() if k not in hidden},
        "requirement_scoreboard": scoreboard,
        "market_snapshot": snapshot,
    }, ensure_ascii=False, sort_keys=True)


def _region_shortcut_applies(card, context):
    """The locked out-of-scope reply answers a Bundesland the candidate typed, or said in a voice note (TASK-210: the
    transcript is their words, as STOP reads it), on an ordinary thread. Not on a thread opened by our template
    (card.campaign) or a declined one: there a named Land is often a decline ('habe schon eine Stelle in Hessen'),
    silence after a decline, or a yes from someone living elsewhere, and the model decides (DECLINE, CAMPAIGN). Not for
    a location pin or a contact card either (their summary text names places)."""
    kind = (context.get("reply_context") or {}).get("kind")
    spoken_or_typed = kind in (None, "text") or bool(context.get("voice_note"))
    return not card.get("campaign") and not card.get("declined") and spoken_or_typed


def _check(bubbles, max_bubbles=MAX_BUBBLES):
    """max_bubbles is MAX_BUBBLES (2) on every ordinary turn; turn() passes 3 for the one turn
    build_warming fired on (TASK-302 point 4) -- the schema (OUTPUT_SCHEMA) documents the wider ceiling,
    this is what actually enforces it, per turn."""
    if not (1 <= len(bubbles) <= max_bubbles):
        raise AssertionError(f"{len(bubbles)} bubbles, the style rule allows 1-{max_bubbles}")
    for b in bubbles:
        if not str(b or "").strip():
            raise AssertionError("empty bubble")
    return [str(b).strip() for b in bubbles]


# --- a rejected reply must never become silence (TASK-375) ---------------------------------------
# Ivan's rules are checked on the outgoing text (app/wa/luna/grounding.py:check_reply). Until now a
# violation raised straight out of turn(): nothing was sent, the pending row kept the error and
# catch-up re-drove the same turn into the same wording. Four shapes of TRUTHFUL German hit that
# path (audit C) and the candidate simply heard nothing.
#
# One corrective retry, then a human. The retry tells the model exactly which rule it broke, in the
# SAME session, so it still has its own tool results in context and rewrites rather than re-derives.
# Its card_patch is deliberately ignored: the candidate's message has not changed, the first pass
# already recorded what was learned from it, and a second reading of the same message is not a new
# finding. If the rewrite breaks a rule too, the candidate gets P.BLOCKED_REPLY_DE and the thread is
# flagged for a colleague -- an answer plus a human, never silence.
#
# ONE OF THE FIVE NO LONGER TAKES THIS PATH AT ALL (ROUND 5, grounding.py's module docstring): the
# exhaustive-claim check ("these are all there are") flags instead of raising, so GR.check_reply's
# own ``flagged`` out-list, not GR.ReplyRejected, is how it reaches turn() below -- the reply is
# sent on the first pass and the thread is flagged next to it, never held for a retry.

CORRECTION_INSTRUCTION = (
    "Your reply was NOT sent: it broke one of the harness's checked rules, which are Ivan's own "
    "(VOLUME, NO INVENTION, COUNT, BRANCHES, LINK, STALE, CONVERGE). The violation is below, in the "
    "harness's words. Write the SAME turn again so that it holds: keep what was true, drop or fix "
    "what broke the rule, and do not argue with the check. You may call a board tool first if the "
    "rule was about evidence you do not have yet. Answer with the same single JSON object as "
    "always -- only your bubbles and next_ask are used from this attempt, the card was already "
    "updated from your first one."
)


def _corrective_payload(violation):
    return json.dumps({"harness_rejected_your_reply": str(violation),
                       "instruction": CORRECTION_INSTRUCTION}, ensure_ascii=False, indent=2)


CLOSING_HINT = (
    "Your reply was NOT sent: its last bubble left the candidate with nothing to answer, so the "
    "conversation would have stalled there. Write the SAME turn again -- same facts, same tone, "
    "nothing withdrawn -- but end it with a closing bubble: the one question, request or next step "
    "the candidate is meant to respond to. If this conversation is genuinely over (they declined, "
    "they cannot be placed, a colleague takes it from here), say that plainly as the last bubble "
    "instead. Answer with the same single JSON object as always."
)


def _closing_hint_payload():
    """Deliberately NOT _corrective_payload: Ivan asked for the same turn attempted once more with a
    short hint, not the harness's complaint quoted back at the model. The session is the same one, so
    the model can still see its own first attempt -- nothing needs to be restated here."""
    return json.dumps({"instruction": CLOSING_HINT}, ensure_ascii=False, indent=2)


class _NotClosed(AssertionError):
    """The closing gate's rejection (app/wa/luna/closing_gate.py). An AssertionError so it travels the
    same except path every other checked rule here already uses, but its own class because the ending
    differs: a second grounding failure holds the reply and pulls in a human, a second CLOSING failure
    sends the reply anyway (Ivan: this gate never escalates). Carries the reply it rejected so that
    send is possible without running the checks a third time."""

    def __init__(self, reply, reason):
        super().__init__(f"CLOSING: the last bubble hands the candidate nothing to answer, so the "
                         f"conversation stalls there (closing gate: {reason})")
        self.reply = reply
        self.reason = reason


def _checked_reply(cl, card, system_text, bubbles, evidence_of, branches, max_bubbles=MAX_BUBBLES):
    """The bubbles that may actually be sent. -> {bubbles, named, evidence, action, escalate_reason,
    flagged}. ``bubbles`` is the model's RAW reply, unchecked (TASK-385, F2): the 1-2 bubble/
    non-empty style check (``_check``) now runs INSIDE ``_run``, below, so a violation on the FIRST
    pass gets the exact same corrective-retry-then-holding-message contract as a GR.check_reply
    guard -- before this it ran ahead of this function, in turn(), and raised straight out of the
    turn uncaught: the one shape in this module that still went silent instead of an answer plus a
    human (the contract every other checked rule here already honours).

    ``max_bubbles`` (TASK-302 point 4): MAX_BUBBLES (2) on every ordinary turn; turn() passes 3 on the
    one turn build_warming fired on, and ONLY that one -- both the first pass and the corrective retry
    inside ``_run`` read the same value from this call's own closure, so a warming turn that trips some
    OTHER rule still gets to keep its three bubbles on the rewrite, and every later turn on the same
    thread is back to 2 without this function needing to know why.

    ``evidence_of()`` rebuilds this turn's evidence from the tool call log (GR.turn_evidence). It is
    called again for the retry rather than reused: the correction tells the model it may look
    something up first, and a rewrite checked against the evidence from BEFORE that call would
    reject the very sentence the call was made to ground.

    ``named`` is the grounded clinic names the sent text carries (the thread's evidence memory);
    ``escalate_reason`` is set only on the path where a colleague has to take over.

    ``flagged`` (ROUND 5, grounding.py's module docstring) is the sentence(s) the exhaustive-claim
    check suspected in whichever bubbles actually went out -- that check no longer blocks, so it
    never causes ``escalate_reason`` to be set here; the caller (turn(), below) records it on the
    card the same way an escalation is recorded, next to the reply that was actually sent.

    CLOSING (Ivan's invariant, 2026-09-24): the last bubble must hand the turn back to the
    candidate. Checked by ``CG.closes_the_turn`` on the bubbles themselves -- what actually goes out
    -- with no branch on the conversation's stage, because the gate's own prompt already treats a
    deliberate ending (declined, not placeable, a colleague takes over) as closing. The version this
    replaced asserted that the model's self-reported ``next_ask`` was non-empty whenever
    ``requirement_scoreboard`` had a gate open; Ivan rejected it for checking a field instead of the
    text, and for exempting every turn with nothing open from the one rule that matters.

    A strictness gradient keyed on requirement_scoreboard briefly lived here and was removed the same
    day, with Ivan's agreement -- see closing_gate.py's docstring for why softening near the end
    turned out to switch leniency on at exactly the consent ask. The gate now reads the bubbles and
    nothing else, which is also what makes it impossible for the same reply to be acceptable in one
    conversation and not in another."""

    def _run(raw_bubbles):
        text_bubbles = _check(raw_bubbles, max_bubbles)
        evidence = evidence_of()
        flagged = []
        named = GR.check_reply(text_bubbles, evidence["names"], deniable=evidence["deniable"],
                               counts=evidence["counts"], counts_by_city=evidence["counts_by_city"],
                               remaining=evidence["remaining"],
                               branches=branches, stale=evidence["stale"],
                               stale_postings=evidence["stale_postings"],
                               shown_before=evidence["remembered"], postings=evidence["postings"],
                               flagged=flagged)
        reply = {"bubbles": text_bubbles, "named": named, "evidence": evidence, "action": None,
                 "escalate_reason": None, "flagged": flagged}
        # Checked last, deliberately: a grounding violation (invented name, stale posting, ...) is
        # about what the text CLAIMS and always takes priority over whether it also asked forward.
        verdict = CG.closes_the_turn(text_bubbles)
        if not verdict.closes:
            raise _NotClosed(reply, verdict.reason)
        return reply

    try:
        return _run(bubbles)
    # AssertionError, not GR.ReplyRejected: ReplyRejected already IS an AssertionError (its own class
    # docstring), and catching the parent here is what lets _check's/_NotClosed's bare AssertionError
    # share this same contract rather than needing a second except clause for it.
    except AssertionError as first:
        violation = first
        # A closing failure gets a hint, a grounding failure gets the violation: the model can see
        # its own first attempt either way (same session), so the hint does not need to restate it.
        payload = _closing_hint_payload() if isinstance(first, _NotClosed) else _corrective_payload(first)
        try:
            out, session_id = cl.reply(system_text, payload, card.get("_session_id"))
            card["_session_id"] = session_id
            return {**_run(out.get("bubbles") or []), "action": "reply_after_correction"}
        # Exception, not AssertionError (workflow finding, 2026-09-24, confirmed by probe): the retry
        # is a whole second model call, and it fails in ways that are not rule violations at all --
        # `claude -p` timing out after LUNA_TIMEOUT_SEC, SessionNotFound, _validate raising on a
        # reply with no card_patch. Those used to escape turn() entirely and the candidate got
        # NOTHING, while a perfectly good, already-checked first reply sat in `first.reply`. A
        # violation costs the model its draft, never the candidate their answer -- that contract has
        # to hold for the retry's own failures too, not just for the rules it breaks.
        except Exception as second:
            # Keyed on FIRST, not second (same finding): what decides whether a human is needed is
            # what was wrong with the reply we are holding, not what went wrong while rewriting it.
            # A turn that entered the retry only because its last bubble trailed off has a truthful,
            # grounded reply in hand; sending it is right even if the rewrite then came back in three
            # bubbles, empty, or not at all. Ivan, explicit: this gate never pulls in a human ("нет
            # такого, что мы каждое сообщение проверяем на гейт, а потом зовем человека"). Prefer the
            # rewrite's own bubbles when the rewrite failed only the closing gate again (_NotClosed
            # carries them, already grounded), else fall back to the first attempt's.
            if isinstance(first, _NotClosed):
                log.error("closing gate rejected the reply and the rewrite did not help (%s); "
                          "sending a checked reply anyway rather than calling a colleague", second)
                return {**getattr(second, "reply", first.reply), "action": "reply_after_correction"}
            # The first reply broke a grounding rule, so it CANNOT be sent whatever happened next: an
            # invented clinic name is not a weak reply, it is a false one. Holding message + a human,
            # exactly as before -- now also when the retry failed for a non-rule reason.
            violation = second
    return {"bubbles": [P.BLOCKED_REPLY_DE], "named": [], "evidence": evidence_of(),
            "action": "reply_blocked_escalated", "flagged": [],
            "escalate_reason": f"two replies in a row broke a checked dialog rule, so the harness "
                               f"answered with its holding message: {violation}"}


def turn(text, thread, button_id=None, client=None, no_send=False):
    """Same contract as app/wa/brain.py:turn() -- {bubbles, buttons, slots, asked, stopped,
    matches, action} -- so app/wa/api.py can call either brain without knowing which one it got.
    ``slots`` here holds the Luna card (a different shape from the deterministic brain's slots;
    app/wa/store.py persists whatever dict it is given), including ``_session_id`` -- the Claude
    Code session this thread is resumed from, invisible to everything except this module.
    ``asked`` is unused by this brain and passed through unchanged so the store's schema does not
    need to know which brain wrote a thread.

    ``thread["turn_context"]`` (``turn_context()``, set by api.process_owed_turn) feeds the payload; when
    the model ran with it, the result carries ``luna_turn`` {at, seen_through_id, model_bubbles} for
    ``turn_marker``. TASK-204: a first ``decline`` from the model, ONLY when app/wa/luna/refusal.py's
    classifier also agrees the candidate's own text is an unambiguous refusal (TASK-385), sends
    P.DECLINE_ACK_DE and sets card.declined/declined_reason/declined_at; a declined card stays silent
    until the model flags ``re_engaged`` (declined cleared, re_engaged_at set, the model's reply goes
    out).
    """
    card = dict(thread.get("slots") or {})
    asked = list(thread.get("asked") or [])
    # TASK-427: set by app/wa/api.py:_ingest_media, consumed by this reply -- never saved back.
    documents_just_received = card.pop("_documents_just_received", [])
    context = dict(thread.get("turn_context") or {})
    seen_through_id = context.pop("seen_through_id", None)

    if SL.is_stop(text):
        return {"bubbles": [], "buttons": [], "slots": card, "asked": asked, "stopped": True,
                "matches": [], "action": "stopped"}

    land = named_non_bavaria_land(text)
    if land and not card.get("region") and _region_shortcut_applies(card, context):
        card["region"] = land
        return {"bubbles": [P.OUT_OF_SCOPE_REGION_DE], "buttons": [], "slots": card, "asked": asked,
                "stopped": False, "matches": [], "action": "out_of_scope_region"}

    # TASK-379/380, TEST THREADS ONLY: the candidate took up the footnote's offer, so the original
    # ads of the postings the last message named go out -- assembled below from board rows by
    # posting_id, so the only link this harness can ever produce is one the code looked up for a
    # thread an operator marked as a test. A production card never has TEST_SOURCES_KEY.
    #
    # A FLAG, NOT A RETURN (TASK-380). This used to take the whole turn before the model ran, so a
    # candidate question that merely mentioned "das Original" was never answered at all -- the links
    # were sent INSTEAD of the answer. The model writes its turn either way now and the links are
    # appended to it.
    wants_sources = bool(thread.get("is_test") and card.get(TEST_SOURCES_KEY)
                         and SRC.asks_for_source(text))

    scoreboard = requirement_scoreboard(card)
    snapshot = market_snapshot(card)
    turn_at = ST.now_iso()
    # TASK-302: computed BEFORE the model runs -- see build_warming. Merged into THIS turn's own
    # snapshot only (market_snapshot's own return value/contract is untouched), so the model sees
    # market_snapshot.warming on exactly the one turn it applies to.
    #
    # STAMPING IS NO LONGER HERE (fix pass, review finding 3 / Ivan's design point d, 2026-09-25):
    # WARMING_KEY used to be written unconditionally the moment a shortlist was computed, before the
    # model had said a word -- so a no_send turn, a two-strikes block, or a plain reply that never
    # used the shortlist at all still spent the thread's one warming turn on nothing. It is now
    # written only after the checked reply actually went out with a valid pick (see the block right
    # after _checked_reply, below). A no_match payload is different: that IS a fact the harness itself
    # already knows this turn (the search really came back empty), so its note is recorded here,
    # unconditionally, same as before -- see build_warming's own no_match/WARMING_NOTE_KEY contract.
    warming = build_warming(card, scoreboard)
    if warming:
        snapshot = {**snapshot, "warming": warming}
        if warming.get("no_match"):
            card[WARMING_NOTE_KEY] = {"kind": "no_match", "criteria": warming["no_match"]["criteria"],
                                      "at": turn_at}
    system_text = P.system_prompt(_CONSTITUTION_TEXT, _QUALIFICATION_TEXT)
    user_text = _user_payload(text, card, scoreboard, snapshot, button_id, documents_just_received, context)

    cl = client or Client()
    cl.phone = thread.get("phone")   # TASK-374: whose CV match_cv_to_postings may read
    cl.role_class = _known_role_class(card)   # TASK-302 point 6: count_postings' own explicit default
    cl.no_send = no_send             # a dry run (shadow_run) keeps show_clinic_photos off the handset
    # TASK-373: where this turn's own board tool calls start in the shared log, read before the model
    # runs -- what it looked up is what its reply may name (app/wa/luna/grounding.py).
    tool_log_offset = GR.log_offset()
    try:
        out, session_id = cl.reply(system_text, user_text, card.get("_session_id"))
    except SessionNotFound:
        # TASK-239: the resumed id's transcript is gone (a disk cleanup, a CLAUDE_CONFIG_DIR move, a
        # host move, or just the CLI's own retention). Nothing conversational survives that -- but the
        # card, scoreboard and snapshot do, and FUNNEL CONTINUITY/PRIOR CONTACT (prompts.py) already
        # exist to make a memoryless restart safe. Clear the id, rebuild the payload so fresh_session
        # reads true, and answer once as first contact. A second failure is not this same recoverable
        # case -- it raises normally.
        card["_session_id"] = None
        user_text = _user_payload(text, card, scoreboard, snapshot, button_id, documents_just_received, context)
        out, session_id = cl.reply(system_text, user_text, None)
    card["_session_id"] = session_id

    patch = dict(out.get("card_patch") or {})
    # Never trust the model's own claim of consent, even if an older session or prompt drift still
    # emits the field (OUTPUT_SCHEMA/OUTPUT_INSTRUCTION no longer describe it at all) -- only an
    # actual button tap, below, may set anonymous_send_consent. The decline, campaign and turn-marker
    # keys are code-owned the same way (TASK-203/101).
    for key in CODE_OWNED_CARD_KEYS:
        patch.pop(key, None)
    was_offered = bool(card.get("anonymous_send_offered"))
    was_ok = card.get("qualification_ok")
    was_declined = bool(card.get("declined"))
    was_branch = card.get("match_branch")
    was_path = card.get("qualification_path")
    card.update(patch)
    # TASK-375 (audit F): the funnel stage is not resettable through a side field while the document
    # that settled the gate is still on the card.
    keep_settled_qualification_path(card, was_path, turn_at)
    # TASK-373: when the candidate picked one of the two branches, stamp when -- the branch itself is
    # the model's reading of their answer, the time it landed is ours.
    if card.get("match_branch") and card.get("match_branch") != was_branch:
        card["match_branch_at"] = turn_at
    # TASK-211: the housing question counts as answered the moment the answer itself is on the card (the yes/no,
    # or a headcount that states a flat is wanted) -- housing_known follows the fact, never stands in for it.
    if housing_needed(card) is not None:
        card["housing_known"] = True
    if was_declined and out.get("re_engaged"):
        card["declined"] = False
        card["re_engaged_at"] = turn_at
    document_reuse = _decide_document_reuse(card, out.get("document_reuse"), turn_at)

    # TASK-333: anonymous_send_consent is never trusted from the model's own card_patch (already
    # stripped from OUTPUT_SCHEMA/OUTPUT_INSTRUCTION, but stripped here too in case an older
    # session or prompt drift still emits it) -- it is set ONLY by an actual tap on one of the two
    # CONSENT_BUTTONS attached below, exactly the "decided in code, not by the model" pattern this
    # module already uses for opt-out/reject/out-of-scope-region.
    if button_id == CONSENT_YES_ID and card.get("anonymous_send_offered"):
        card["anonymous_send_consent"] = True
    elif button_id == CONSENT_NO_ID and card.get("anonymous_send_offered"):
        card["anonymous_send_consent"] = False
    just_offered = bool(card.get("anonymous_send_offered")) and not was_offered
    # A tap on the consent 'Nein danke' turns down the profile share, never the contact: no decline ack, no silence
    # (prompts DECLINE; live 2/2 the tap became a terminal decline, review 2026-09-14).
    consent_no_tap = button_id == CONSENT_NO_ID and bool(card.get("anonymous_send_offered"))

    # TASK-380 (audit F, the second side door): a card_patch declaring the candidate unplaceable is
    # said out loud whichever field it uses. ``qualification_ok: false`` always was; the schema-legal
    # ``qualification_path: "reject"`` was not wired to anything, so it regressed the stage from
    # consent to qualification in silence while the model's ordinary bubble went out unchanged. The
    # path is read off the CARD, after keep_settled_qualification_path has had its say, so a
    # "reject" the card's own documents contradict was already refused and says nothing.
    says_not_placeable = ((patch.get("qualification_ok") is False and was_ok is not False)
                          or (card.get("qualification_path") == "reject" and was_path != "reject"))

    raw_bubbles = out.get("bubbles") or []
    buttons = []
    model_bubbles = []
    checked = None   # set only in the _checked_reply branch below; read by the warming-outcome block

    decline_candidate = bool(out.get("decline")) and not was_declined and not consent_no_tap
    decline_now = False
    if decline_candidate:
        # TASK-385: a decline flag alone no longer ends the conversation. Ivan's rule: only an
        # UNAMBIGUOUS refusal does -- a maybe, a qualified yes, a deferral or a question back keeps it
        # alive -- so a second, independent model call judges the candidate's own text first
        # (app/wa/luna/refusal.py, written for exactly this call site; runs only here, never on every
        # turn). Every classifier failure mode (timeout, missing binary, unparseable/ambiguous answer)
        # already resolves to Verdict(False, ...) -- keep talking, never a wrongly-ended conversation
        # -- and is recorded here either way, so a silent classifier outage is visible on the thread,
        # not only in the logs.
        #
        # TASK-386: the classifier is stateless -- it never sees this thread's history -- so a bare
        # "nein" was indistinguishable from an answer to our own gate question (Urkunde, Bayern, a
        # city, housing) and from a refusal of the campaign opener asking if the search is still on.
        # our_last_message is the one fact that makes the two decidable: the literal text of the last
        # thing this candidate actually read (turn_context's own last_outbound, not the model's
        # memory), or None with no prior outbound at all -- SYSTEM_PROMPT then reads text alone, same
        # as before this fix.
        our_last_message = (context.get("last_outbound") or {}).get("text")
        verdict = RF.is_unambiguous_refusal(text, our_last_message=our_last_message)
        if verdict.is_refusal:
            decline_now = True
        else:
            # FLAG, not escalate (2026-09-22, Ivan's predictable-escalation-list round): a
            # disagreement between two automated judges, on a thread that is still talking
            # normally, is not a reason to pull a human in -- but it is worth a look, so it is
            # still recorded, just under the tier that never sets _escalated.
            ESC.record_flag(card, ESC.DECLINE_CLASSIFIER_DISAGREEMENT,
                            f"model flagged decline=true but the refusal classifier disagreed "
                            f"({verdict.reason}) -- conversation continued")
    if decline_now:
        # TASK-204: one fixed acknowledgement (Ivan 2026-09-14, the old bot's wording), then silence.
        card.update(declined=True, declined_reason=out.get("decline_reason"), declined_at=turn_at)
        bubbles = [P.DECLINE_ACK_DE]
        action = "decline_ack"
    elif card.get("declined"):
        bubbles = []
        action = "declined_no_send"
    elif says_not_placeable:
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
    elif (out.get("action") == "explain_not_placeable"
          and (card.get("qualification_ok") is False or card.get("qualification_path") == "reject")):
        # TASK-380 follow-up: ``says_not_placeable`` above only catches the turn the card FIRST
        # becomes not-placeable. A candidate who was already not-placeable before this turn and
        # writes again (e.g. resends the identical opener) gets no new patch to trip that flag --
        # but the model still comes back with its own action, "explain_not_placeable", and its own
        # paraphrase, which is exactly the locked wording this gate exists to guarantee
        # ("not placeable -> explain once, then stop", VENDORED.md; module docstring point 2
        # above: never the model's own phrasing for this gate). The no_send/empty branch above
        # already took priority, so a model that means to stay silent on a repeat still does
        # (test_luis_reopening_with_the_identical_message_does_not_re_litigate_from_scratch); this
        # branch only catches the case where it chose to speak instead. Reusing ``out["action"]``
        # rather than a new field, same as the else branch below reads it.
        bubbles = [P.REJECT_BODY_DE]
        action = "explain_not_placeable"
    else:
        # TASK-373/375, the rules a prompt cannot guarantee: at most OFFER_LIMIT POSITIONS in one
        # message, every clinic it names really returned by a tool on this thread (or sitting in the
        # harness offer, and still live on the board), how many more matched, both branches on the
        # offer turn, and no link. A violation costs the model its draft, never the candidate their
        # answer (_checked_reply).
        shown_before = list(card.get(GROUNDED_KEY) or [])
        # Fix pass (live 2026-09-25, second round): computed once, outside the closure below, since
        # evidence_of() may run twice in one turn (the corrective retry) and this is a live job_detail
        # call -- no point re-fetching the same posting for both passes. None on the warming turn
        # itself (WARMING_PICK_KEY is not written until the outcome block, further down, well after
        # this point) and every turn without a pick at all; see _warming_pick_echo.
        warming_pick_echo = _warming_pick_echo(card)

        def evidence_of():
            return GR.turn_evidence(snapshot, GR.calls_since(tool_log_offset), shown_before,
                                    phone=thread.get("phone"), inbound=text,
                                    known_postings=list(card.get(GROUNDED_POSTINGS_KEY) or []),
                                    warming_pick_echo=warming_pick_echo)

        # Both branches are only a real choice once the harness has an offer to narrow or pool; mid
        # funnel there is no result set to be put forward to (market_snapshot, offer is null).
        # raw_bubbles goes in unchecked (TASK-385, F2): _checked_reply's own _run() applies the 1-2
        # bubble style check now, so a violation on the first pass is a corrective retry, not an
        # exception straight out of turn(). TASK-302 point 4: 3 bubbles, not 2, ONLY on the turn
        # `warming` fired on -- every other turn (including this thread's very next one) stays at
        # MAX_BUBBLES.
        # TASK-302 point c: 3 bubbles, not 2, ONLY when a real shortlist ("candidates") was shown this
        # turn -- a no_match turn (an ordinary card-advancing question, no pick to make) stays at
        # MAX_BUBBLES, same as every turn without market_snapshot.warming at all.
        checked = _checked_reply(cl, card, system_text, raw_bubbles, evidence_of,
                                 bool(snapshot.get("offer")),
                                 max_bubbles=3 if (warming or {}).get("candidates") else MAX_BUBBLES)
        bubbles = model_bubbles = checked["bubbles"]
        named = checked["named"]
        # TASK-302 fix pass (review finding 1 / design point e, 2026-09-25): a word only ALLOWED for
        # this turn (a compound quoted from a warming candidate's title/description -- grounding.py's
        # ``turn_only``) must never be written to this permanent memory, or the very next "ist die
        # Stelle noch frei" reads it back as a house the thread has since LOST (STALE) -- exactly the
        # live bug commit 5727922 introduced by seeding it into the same set as real clinic names.
        turn_only = checked["evidence"].get("turn_only") or set()
        persist_named = [n for n in named if GR.fold(n) not in turn_only]
        if persist_named:
            card[GROUNDED_KEY] = sorted({*(card.get(GROUNDED_KEY) or []), *persist_named})
            # TASK-380: the POSTINGS those names were grounded on, so the next turn's STALE re-check
            # is about the opening (Ivan's rule (a)) and not only about the house keeping any
            # opening at all.
            grounded_postings = {checked["evidence"]["postings"][GR.fold(n)]
                                 for n in persist_named if GR.fold(n) in checked["evidence"]["postings"]}
            if grounded_postings:
                card[GROUNDED_POSTINGS_KEY] = sorted({*(card.get(GROUNDED_POSTINGS_KEY) or []),
                                                      *grounded_postings})
        if checked["escalate_reason"]:
            # The code-level two-strikes safety net: a harness limitation (the model could not
            # produce a compliant reply twice running), never a topic the candidate raised --
            # its own fixed code, distinct from anything the model itself may ask to escalate for.
            ESC.record_escalation(card, ESC.GROUNDING_RULE_VIOLATED_TWICE, checked["escalate_reason"])
        elif checked["flagged"]:
            # ROUND 5 (grounding.py's module docstring): the exhaustive-claim check suspected one of
            # these sentences but did not block -- the reply above already went to the candidate.
            # FLAG, not escalate (2026-09-22 round): a suspicion on a reply that already went out
            # is worth a look, not a reason to pull a human into a conversation that is fine.
            ESC.record_flag(card, ESC.EXHAUSTIVE_CLAIM_SUSPECTED,
                            "reply may not disclose the full remainder (reply already sent): "
                            + "; ".join(repr(s) for s in checked["flagged"]))
        # TASK-379: on a test thread only, offer the original ad of what this message named.
        sources = SRC.sources_for(named, checked["evidence"]["postings"]) if thread.get("is_test") else []
        if sources:
            bubbles = [*bubbles[:-1], bubbles[-1] + SRC.FOOTNOTE_DE]
            card[TEST_SOURCES_KEY] = sources
        # What the candidate actually receives, footnote included -- turn_marker matches the
        # outbound rows by body, so a bubble recorded without its footnote would come back in the
        # next payload as a message somebody else sent.
        model_bubbles = bubbles
        action = checked["action"] or str(out.get("action") or "reply_now_conversational")
        if checked["escalate_reason"] and just_offered:
            # The model's own text never reached the candidate, so nothing asked them for consent:
            # leaving the flag set would attach the two buttons to the holding message, and would
            # also make the NEXT turn's ask no longer "just offered", so the buttons would never
            # appear again and consent could not be given at all.
            card["anonymous_send_offered"] = was_offered
            just_offered = False
        if just_offered:
            # The turn where the model just asked for the anonymized send: attach real, tappable
            # buttons rather than leaving consent to however the candidate happens to phrase "yes".
            buttons = list(CONSENT_BUTTONS)

    # TASK-302 fix pass (review finding 3 / Ivan's design point d, 2026-09-25): what THIS turn's
    # shortlist (if any) actually did, recorded once here rather than in every branch above --
    # SUCCESS (WARMING_KEY + WARMING_PICK_KEY) only when the checked reply really went out, was
    # exactly three bubbles, and named a pick from the shown shortlist; DECLINE (a reason, no pick)
    # excludes the whole shown shortlist from every later one so it is never retried; anything else
    # -- no_send/empty, the fixed decline/not-placeable text instead of the model's own reply,
    # blocked/escalated, a missing or invalid pick, the wrong bubble count -- writes only a visible
    # failure note and nothing permanent, so build_warming fires again next turn unchanged.
    if warming and warming.get("candidates"):
        candidate_ids = {c["posting_id"] for c in warming["candidates"]}
        pick, why = out.get("warming_pick"), out.get("warming_why")
        note = None
        if not bubbles:
            note = {"kind": "failure", "reason": "no_send or empty bubbles", "at": turn_at}
        elif action in ("decline_ack", "explain_not_placeable"):
            note = {"kind": "failure", "at": turn_at,
                    "reason": f"the turn ended in {action!r} (a fixed reply), never the model's own "
                              f"checked warming bubbles"}
        elif checked is None:
            note = {"kind": "failure", "at": turn_at,
                    "reason": f"action={action!r} never reached the grounding check"}
        elif checked["escalate_reason"]:
            note = {"kind": "failure", "reason": checked["escalate_reason"], "at": turn_at}
        elif pick is not None and pick in candidate_ids and len(checked["bubbles"]) == 3:
            card[WARMING_KEY] = turn_at
            card[WARMING_PICK_KEY] = pick
            card.pop(WARMING_NOTE_KEY, None)
        elif pick is None and why:
            card[WARMING_DECLINED_KEY] = sorted({*(card.get(WARMING_DECLINED_KEY) or []), *candidate_ids})
            note = {"kind": "decline", "reason": why, "at": turn_at}
        else:
            note = {"kind": "failure", "at": turn_at,
                    "reason": f"warming_pick={pick!r} not valid for the shown ids "
                              f"{sorted(candidate_ids)!r}"}
        if note is not None:
            card[WARMING_NOTE_KEY] = note

    if out.get("escalate_to_manager"):
        # 2026-09-22 (Ivan's predictable-escalation-list round): the model names WHICH of the
        # closed codes applies (escalate_reason_code) -- a code outside that set is never honoured
        # as a real escalation, so the model cannot invent its own reason to pull a human in. Either
        # way this is additive, via record_escalation/record_flag (TASK-385 F1's own discipline): a
        # fact this same turn already recorded above (a refused decline, a demoted exhaustive-claim
        # flag, a two-strikes blocked reply) survives next to whatever this call adds.
        ESC.record_model_escalation(card, out.get("escalate_reason_code"), out.get("escalate_reason"))

    # TASK-379/380: the candidate asked for the original ads, so they go out NEXT TO whatever the
    # turn already had to say -- appended in code, from board rows, re-resolved against the live
    # board at this moment. A declined thread stays silent; everything else answers both.
    if wants_sources and not card.get("declined"):
        live, gone = SRC.live_sources(card.get(TEST_SOURCES_KEY) or [])
        bubbles = [*bubbles, SRC.link_bubble(live, gone)]
        model_bubbles = bubbles
        action = action if model_bubbles[:-1] else "test_source_links"

    # TASK-373: the funnel stage the card is in after this turn, and when it entered it. Recomputed
    # from the gates (funnel_stage), never carried forward blindly -- a stage that moved backwards
    # because a fact was corrected is a real state change and gets its own timestamp.
    stage = funnel_stage(requirement_scoreboard(card))
    if card.get("stage") != stage:
        card["stage"], card["stage_at"] = stage, turn_at

    result = {"bubbles": bubbles, "buttons": buttons, "slots": card, "asked": asked, "stopped": False,
              "matches": snapshot.get("matches") or [], "action": action}
    if seen_through_id is not None:
        result["luna_turn"] = {"at": turn_at, "seen_through_id": seen_through_id, "model_bubbles": model_bubbles}
    if document_reuse:
        result["document_reuse"] = document_reuse
    return result


# --- reuse of documents imported from the earlier contact (TASK-342) ------------------------------------

def _decide_document_reuse(card, reuse, at):
    """Apply the model's ``document_reuse`` {confirmed_ids, declined_ids} to the imported entries of
    card["documents"] (a new list, entries copied). -> the changes [{id, state, previous, at}] for
    ``apply_document_reuse``. Naming an id twice is a no-op; an id that is not an imported document on this card,
    or in both lists, raises."""
    if not reuse:
        return []
    if not isinstance(reuse, dict):
        raise RuntimeError(f"document_reuse must be an object, got {reuse!r}")
    confirmed, declined = list(reuse.get("confirmed_ids") or []), list(reuse.get("declined_ids") or [])
    both = set(confirmed) & set(declined)
    if both:
        raise RuntimeError(f"document_reuse names ids {sorted(both)} as both confirmed and declined")
    card["documents"] = [dict(d) for d in card.get("documents", [])]
    imported = {d["id"]: d for d in card["documents"] if d.get("imported")}
    unknown = [i for i in confirmed + declined if i not in imported]
    if unknown:
        raise RuntimeError(f"document_reuse names ids {unknown} that are not imported documents on the card "
                           f"(imported: {sorted(imported)})")
    changes = []
    for state, ids in ((REUSE_CONFIRMED, confirmed), (REUSE_DECLINED, declined)):
        for doc_id in dict.fromkeys(ids):
            entry = imported[doc_id]
            if entry.get("reuse") == state:
                continue
            changes.append({"id": doc_id, "state": state, "previous": entry.get("reuse"), "at": at})
            entry.update(reuse=state, reuse_decided_at=at)
    return changes


def _remove_segment(value, text):
    """``value`` without the one ``text`` segment an earlier "\\n\\n".join appended; raises when it is not there."""
    index = (value or "").find(text)
    if index < 0:
        raise RuntimeError("the confirmed document's text is no longer on the card")
    before, after = value[:index], value[index + len(text):]
    if before.endswith("\n\n"):
        before = before[:-2]
    elif after.startswith("\n\n"):
        after = after[2:]
    return before + after


def apply_document_reuse(c, t, changes):
    """Record ``_decide_document_reuse``'s changes on the wa_documents rows (reuse_state, reuse_decided_at) and on
    the card text keys: a confirmed document's stored text is appended to its text_key (cv_text/urkunde_text, the
    TASK-427 key), a confirmation withdrawn removes it again. Called by api.process_owed_turn after the send."""
    for change in changes:
        row = ST.document_by_id(c, change["id"])
        if row is None or row["phone"] != t["phone"]:
            raise RuntimeError(f"wa_documents {change['id']} is not a document of {t['phone']}")
        ST.set_document_reuse(c, change["id"], change["state"], change["at"])
        key, text = row["text_key"], row["text"]
        if not (key and text):
            continue
        slots = t["slots"]
        if change["state"] == REUSE_CONFIRMED:
            slots[key] = "\n\n".join(filter(None, (slots.get(key), text)))
        elif change["previous"] == REUSE_CONFIRMED:
            slots[key] = _remove_segment(slots.get(key), text)
            if not slots[key]:
                del slots[key]
