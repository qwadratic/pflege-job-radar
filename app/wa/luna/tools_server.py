"""A stdio MCP server exposing the board read API to Luna as real tools, so a turn can look
something up mid-conversation instead of only ever reasoning from the pre-computed
``market_snapshot``/``requirement_scoreboard`` (app/wa/luna_brain.py). Started by the ``claude`` CLI
itself via ``--mcp-config`` (see ``luna_brain.py:Client._live_reply``, which generates the config
pointing at ``python -m app.wa.luna.tools_server`` using the same interpreter the harness itself runs
under, so the ``mcp`` package is guaranteed to be on its path) -- this module is never imported by the
rest of the app, only ever run as that subprocess.

Three kinds of tool (TASK-213, Ivan 2026-09-16, after the housing filter existed for a task and was
never once used by the model):

1. ``search_postings``/``get_posting``/``list_clinics`` -- the general board queries.
2. Purpose-built tools with the filter already preset -- ``search_postings_with_housing``,
   ``list_clinics_with_housing``, ``list_cities_with_postings``, ``count_postings``,
   ``match_cv_to_postings``. A filter the model has to assemble itself out of bare parameter names
   goes unused; a tool whose name *is* the candidate's need does not.
3. The fallback for everything the presets do not cover: ``read_board_docs`` (this repo's own agent
   documentation, skill/SKILL.md + skill/references/*.md) and ``board_api_get`` (an allowlist of
   public board GET paths, answered in-process by the same functions those routes call). Both fail
   loudly: an unknown topic or a path off the allowlist is a ToolError naming what is allowed.

Every tool description carries the vocabulary needed to call it correctly -- departments,
Regierungsbezirke, role classes, employment types, what the housing mark means and how much of the
board carries it -- and that vocabulary is read off the live board (``board_vocabulary.py``), never
hardcoded here: a list written into this file rots the first time the board changes, and a wrong
value silently returns zero rows. The counting happens in the PARENT process, which already holds a
warm snapshot, and arrives here as a file (``WA_LUNA_BOARD_VOCABULARY``): building it here put a cold
Supabase build (8-17s measured) on the CLI's 30s MCP connect deadline, on every turn, tool-less ones
included (TASK-213 review). The board rows the tools actually query travel the same way
(``WA_LUNA_BOARD_SNAPSHOT``, TASK-273): without it, every board tool -- not just the vocabulary text --
paid that same cold build on its own first call, which TASK-213's fix never touched. On the CLI's first
``tools/list`` request -- proof it actually received the schemas, not just that this process started
(TASK-274) -- this server stamps ``WA_LUNA_TOOLS_READY``, which is how ``luna_brain._live_reply`` sees
that the turn actually had the board tools -- a server that dies or is dropped before that point
leaves the CLI exiting 0 with a normal-looking reply and no stamp behind it.

What a tool may hand back (TASK-145, Ivan 2026-09-21, after the first real phone-rail conversation):

* At most ``LISTING_LIMIT`` positions per listing call, next to the true number of matches
  (``{shown, total}``). The cap is here, where the result set is assembled, so no prompt edit and no
  tool argument can raise it, and a short list can never read as the whole market.
* Live-verified postings only, with no way around it: there is no ``verify=`` escape hatch on the raw
  query door any more and ``get_posting`` reads the same base. A posting whose liveness is not
  confirmed is reported as withheld, never silently dropped.
* The candidate's own word for a town is resolved to the board's spelling the way their word for a
  department already is (``_resolve_city``); a word the board has no town for is an error naming the
  nearest spellings, never an empty result that reads as "nothing open there".
* ``get_posting`` returns the ad itself (description, requirements, pay, language, the flat's own
  wording), not a search row: everything beyond clinic/city/department is invention unless it came
  from there.

Every call is appended to a JSONL log (``config.LUNA_SESSION_DIR/tool_calls.jsonl``) so a test can
prove a tool was actually invoked -- not just that the reply happened to look right afterward. No
candidate number and no message body ever enters that log or a tool result.

Reuses the exact same query functions the rest of the harness already relies on: ``D.filter_jobs``/
``D.filter_clinics`` (the same ones GET /api/jobs and /api/clinics use, and the same ones
``app/wa/brain.py:jobs_for`` calls), so a tool call and the harness's own pre-computed snapshot can
never drift against each other.

Must be launched as ``python -m app.wa.luna.tools_server`` with the repo root as (or on the path
of) the working directory -- a relative import needs real package context, so running this file
directly (``python tools_server.py``) cannot work no matter what sys.path says. See
``luna_brain.py``'s mcp-config generator for the exact command/cwd this is started with.
"""
import difflib
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ... import data as D
from ... import search as SE
from .. import bridge as BR
from .. import config as C
from .. import slots as SL
from .board_vocabulary import LIVE_BASE, city_of, clinic_key, clinic_name_of, vocabulary_lines

# This process is a fresh subprocess the CLI spawns -- a test's monkeypatch on the *parent*
# process's config.SQLITE_PATH/LUNA_SESSION_DIR never reaches this import. luna_brain.py's
# mcp-config generator passes the parent's current values through as env vars for exactly that
# reason; apply SQLITE_PATH here (once, at import) since app.wa.store reads it as a plain
# module attribute. LUNA_SESSION_DIR (used only for this module's own call log) is resolved per
# call instead, via _session_dir() below.
if os.environ.get("WA_SQLITE_PATH"):
    C.SQLITE_PATH = Path(os.environ["WA_SQLITE_PATH"])

try:
    from . import contacts as CT   # TASK-168, built in parallel -- absent until that task lands
except ImportError:
    CT = None

_REPO_ROOT = Path(__file__).resolve().parents[3]

mcp = MCPServer("jobs")   # same name as luna_brain.MCP_SERVER_NAME; model-visible, no brand

# LIVE_BASE (board_vocabulary.py): open postings the verifier re-fetched, newest first -- the base of
# every posting query here and of app/wa/slots.py:filters()/market_snapshot, so a tool result and the
# harness's own shortlist count the same rows.
RESULT_LIMIT = 50

# Ivan 2026-09-21 (TASK-145; the dialog side of the same rule is TASK-144): a candidate never gets a wall
# of vacancies -- at most five positions in one listing turn. It is a hard number, not a default: the
# listing tools take no limit argument at all, so nothing the model writes and no prompt edit can raise
# it. The count of what matched travels next to the rows (_listing) precisely because the list is short:
# five of a hundred read as "that is all there is" unless the hundred is said in the same breath.
LISTING_LIMIT = 5

# Below this ratio a "near match" is a different town, and naming one invites the model to offer it as the
# place the candidate asked for (measured with slots._fold: 'nuremberg'/'Nürnberg' 0.78, 'hamburg'/'Bamberg'
# 0.71). Not a ceiling on any result -- it decides only which spellings the refusal names.
CITY_NEAR_MATCH_RATIO = 0.75

# The ad itself, for get_posting. None of these is in the snapshot's JOB_COLS projection except enr_tariff
# and enr_pay_grade, so they are read through app/data.py:job_detail -- the board's own GET /api/jobs/{id}
# read of the postings row (description and the enrichment excerpts live there only).
#
# shift_night_weekend is deliberately NOT here any more (requirements audit 2026-09-21, section 4): the
# column is populated on 0 of 2462 live postings, while this tool's description advertised it and its own
# rule says a null field "means this ad did not say it". Advertising a column the board never fills invites
# the model to ask for shifts and then read an always-null answer as "this ad is silent about shifts",
# 2462 times out of 2462. It goes back the day the board fills it, together with a real count.
POSTING_DETAIL_FIELDS = ("description", "enr_requirements", "enr_experience", "enr_language_req",
                         "qualification_hint", "enr_tariff", "enr_pay_grade", "enr_housing_evidence",
                         "start_date", "contract")


def _session_dir():
    """This server runs as a subprocess the CLI spawns fresh -- it does its own import of
    app.wa.config, so a test's ``monkeypatch.setattr(config, "LUNA_SESSION_DIR", ...)`` in the
    *parent* process never reaches it. ``luna_brain.py:_mcp_config_path`` passes the parent's
    current value through explicitly as the WA_LUNA_SESSION_DIR env var for exactly this reason;
    fall back to the config default only if that is somehow unset (e.g. this file run by hand)."""
    override = os.environ.get("WA_LUNA_SESSION_DIR")
    return Path(override) if override else C.LUNA_SESSION_DIR


def _log_call(name, args):
    """Append-only, and it RAISES on a write failure (TASK-146).

    It used to swallow OSError as "best-effort", which was true until TASK-144 made this log the
    sole source of grounding evidence. Since then a failed write leaves the tool returning rows
    normally while ``grounding.calls_since`` sees nothing, so every truthful clinic name in the
    reply is rejected as an invention -- every turn on the service, until someone reads a traceback
    about NO INVENTION and guesses at a disk or permission problem. A logging failure has to look
    like a logging failure (CLAUDE.md: failures fail loudly and get recorded).
    """
    d = _session_dir()
    d.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"tool": name, "args": args, "at": time.time()}, ensure_ascii=False)
    with open(d / "tool_calls.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _job_row(r):
    # No source_url / external_url (TASK-146). offer.py deliberately keeps the link out of the
    # payload the model writes from -- "the way to make that a guarantee rather than a rule is to
    # keep the link out" -- while every tool-sourced row handed one over, and check_reply does not
    # reject a URL in a bubble. Nothing in the dialog path used it.
    #
    # housing_kind and childcare (requirements audit 2026-09-21, sections 2 and 4). `housing` alone was
    # a false promise on 61 of the 497 marked open postings, which offer only help with the search or
    # the moving costs; and enr_childcare is populated on 2170 of 2560 open postings and was exposed by
    # no tool at all, so "gibt es eine Kita?" could only be escalated to a human.
    return {"posting_id": r.get("posting_id"), "title": r.get("title"),
            "clinic_id": r.get("clinic_id"), "clinic_name": r.get("clinic_name") or r.get("employer"),
            "city": D.town_of(r), "department": r.get("department_hint"),
            "regierungsbezirk": r.get("regierungsbezirk"), "housing": bool(r.get("enr_housing")),
            "housing_kind": D.housing_kind(r), "childcare": r.get("enr_childcare"),
            "employment_types": r.get("employment_types")}


_city = city_of                 # one clinic/city identity for every count here and in the vocabulary
_clinic_name = clinic_name_of


def _limit(value, default=10):
    return max(1, min(int(value or default), RESULT_LIMIT))


def _listing(rows, project=_job_row, town=None):
    """What a posting listing hands the model: the first LISTING_LIMIT rows, and how many matched in all.

    ``total`` is the whole match count, never len(shown): the model has to be able to say "und 95 weitere"
    and to offer narrowing the search, which it cannot do from a truncated list (TASK-145/TASK-144).

    ``town`` rides along whenever a town was asked for, because the board's spelling is regularly not the
    candidate's ('Lohr a. Main' for "Lohr am Main", 'Hausham' for "Landkreis Miesbach") and the reply has
    to be able to say which town it is actually answering about."""
    out = {"shown": [project(r) for r in rows[:LISTING_LIMIT]], "total": len(rows)}
    if town:
        out["town"] = _town_said(town)
    return out


def _town_said(town):
    """The resolution, as the model gets to see it: what was asked, how the board writes it, and whether
    it was matched as a town or through the registry's Landkreis column."""
    return {"asked": town["asked"], "board_spellings": town["spellings"], "matched_as": town["matched"]}


def _cities_with_postings():
    """The board's own spelling of every town that has an OPEN posting -- both fields
    app/data.py:filter_jobs matches a city against.

    Open, not live-verified, and that is the fix rather than a loosening (TASK-146). The tools
    search live-verified rows, but this set answers a different question: is this a town the board
    knows at all? Building it from live rows only meant a town with open postings that the verifier
    no longer confirms was reported to the model as a town the board does not have -- with an error
    that says in so many words "this is NOT the same as nothing being open there" and sends it off
    to ask the candidate to re-spell their own town, or offers a spelling-near DIFFERENT town as
    the one they meant. Town known with nothing live in it must answer total=0. The live filter
    still applies to the rows; it just stops deciding whether the town exists.
    """
    return {c for r in D.jobs()
            for c in (city_of(r), (r.get("clinic_town") or "").strip()) if c}


def _live_clinics(rows):
    """Clinic rows that really have a live-verified open posting (TASK-146).

    ``app/data.py:filter_clinics``'s ``has_jobs`` counts ``jobs_open``, which includes postings the
    verifier no longer confirms -- so a clinic whose only posting is gone reached the model as a
    clinic with openings, and ``grounding.clinics_returned`` then turned its name into valid
    evidence for saying so to a candidate. ``jobs_live`` is aggregated right next to it
    (app/data.py:249) and was unused. The posting doors have refused withheld rows since TASK-145;
    this is the same rule on the clinic doors, which that task did not reach.
    """
    return [c for c in rows if (c.get("jobs_live") or 0) > 0]


def _registry_towns():
    """Every town the registry has a clinic in -- what app/data.py:filter_clinics matches a city against."""
    return {(c.get("town") or "").strip() for c in D.clinics() if (c.get("town") or "").strip()}


#: Splits a candidate's multi-town phrase into individual town words -- ONLY tried as a fallback
#: after the whole string has already failed to resolve as one town (see _resolve_city), so a real
#: single-spelling town that happens to contain one of these ('Neuburg/Donau', 'Bad Kissingen') is
#: never touched: it always resolves on the first attempt and this pattern is never reached for it.
_MULTI_CITY_SPLIT_RE = re.compile(
    r"\s*(?:,|;|/|\+|&|\boder\b|\bund\b|\bor\b|\band\b|\bbzw\.?\b|\bsowie\b)\s*", re.I)


def _resolve_one_city(word, known, what):
    """The candidate's own word -> every board spelling of the ONE town it names, or a ToolError.

    -> app/data.py:town_spellings' dict ({asked, spellings, matched}), which is what the tool hands back
    next to the rows so the reply can name the town the way the board writes it.

    TASK-145 resolved a word by asking whether a BOARD spelling occurs inside it (slots.read_city), which
    answered München/Muenchen/Munchen and 'Landkreis Coburg' and nothing else. The requirements audit
    (2026-09-21) broke it four ways with correctly spelled Bavarian towns that have live postings:
    'Weißenburg' (3), 'Lohr am Main' (12), 'Neumarkt' (11) and 'Landkreis Miesbach' (8) were all refused
    as "not a town this board has open postings in", and 'Neuburg an der Donau' found 1 posting while 50
    sat in the same town under the board's other spelling 'Neuburg/Donau'. The class of failure is in
    app/data.py:town_spellings, which is where the fix is; read_city stays here as the reader for a word
    that is really a phrase ('in München bitte', where no normalisation of the whole string can match).

    An unrecognised word is an error, never []: those two are indistinguishable to the model, and only one
    of them may be told to the candidate. The nearest spellings are named so it can ask which was meant --
    with the warning, because a near match IS a different town (Bamberg is not Hamburg)."""
    found = D.town_spellings(word, known, D.landkreis_towns())
    if found["status"] == "unknown":
        inside = SL.read_city(word, known)           # the word was a phrase with a town in it
        if inside:
            found = D.town_spellings(inside, known)
            found["asked"] = word
    if found["status"] == "resolved":
        return found
    if found["status"] == "ambiguous":
        raise ToolError(f"{word!r} names {len(found['spellings'])} different towns this board has {what} in "
                        f"({', '.join(found['spellings'])}) and nothing was searched. Ask the candidate which "
                        f"one they mean -- do not pick one. Internal tool note, never quote it verbatim.")
    by_fold = {SL._fold(c): c for c in known}        # the same fold read_city matched with, for difflib
    near = [by_fold[f] for f in difflib.get_close_matches(SL._fold(word), list(by_fold), n=5,
                                                          cutoff=CITY_NEAR_MATCH_RATIO)]
    raise ToolError(f"{word!r} is not a town this board has {what} in, so nothing was searched -- this is NOT "
                    f"the same as nothing being open there. "
                    + (f"Spelling-nearest board towns: {', '.join(sorted(near))} -- each is a DIFFERENT town, "
                       f"so ask which one is meant instead of answering about it. "
                       if near else "No board town resembles it (the board is Bavaria only). ")
                    + "list_cities_with_postings names the towns that do have postings. Internal tool note, "
                      "never quote it to the candidate.")


def _resolve_city(word, known, what):
    """The candidate's own word -> every board spelling of every town it names, or a ToolError.

    A candidate naming two or more towns in one breath ('München oder Nürnberg', 'Augsburg, Ingolstadt')
    is common and the board genuinely has postings in both -- TASK-131-adjacent live finding, 2026-09-22:
    a candidate offered 'München oder Nürnberg' broke the dialog. Before this fix the whole phrase went
    straight to _resolve_one_city, whose own phrase-fallback (slots.read_city, meant for 'in München
    bitte') scans the WHOLE known-town list for any embedded match and keeps the LONGEST one -- for a
    two-town phrase that finds both towns embedded and silently keeps only one of them (whichever
    board spelling is longer), so the reply's evidence covers a single town while the candidate asked
    about two, and a truthful combined figure has no evidence behind it at all.

    A whole string that IS ITSELF one of the board's own literal spellings resolves first, unsplit --
    checked by folded membership in ``known`` directly, not by ``D.town_spellings``'s own status: that
    function tokenises on the same punctuation this splits on, and answered 'resolved' -- spellings
    truncated to the FIRST town only -- for both 'München, Augsburg' (the comma reads as no qualifier
    at all) and 'München/Augsburg' (the slash reads as a qualifier, the shape a real single-town
    spelling like 'Neuburg/Donau' uses), which would have made those two exact separators silently
    keep answering for one town forever. Folded membership has no such tokeniser to fool: 'Neuburg/
    Donau' resolves here because it is, verbatim, a board spelling; 'München/Augsburg' is not, so it
    reaches the split below. Every existing single-city caller keeps its exact behaviour either way.

    Only when the whole string is not itself a known spelling AND it contains one of this module's own
    multi-town separators is the phrase split and each piece resolved through the FULL
    _resolve_one_city (phrase-fallback included, so 'die Stadt München' still resolves as its own
    piece). Every piece has to resolve for the split to succeed: one bad or ambiguous piece surfaces
    THAT piece's own named error rather than silently answering for only the town that did resolve. No
    separator found -- falls through to _resolve_one_city on the whole original string, unchanged from
    before this fix (a bare ambiguous base town like the five Neustadts lands here, exactly as before).

    -> the same {asked, spellings, matched} shape every caller already reads, 'matched' set to 'multi'
    on a split and 'spellings' the union of every resolved town's own board spellings, so _town_rows's
    existing `any(... for s in spellings)` matching includes a posting in EITHER town with no change to
    _town_rows, _job_filters, _job_rows or count_postings -- and grounding.py's replay (which re-runs
    this exact call) then recomputes the same, correct, combined count as evidence."""
    if SL._fold(word) in {SL._fold(k) for k in known}:
        return _resolve_one_city(word, known, what)
    parts = [p for p in _MULTI_CITY_SPLIT_RE.split(word) if p.strip()]
    if len(parts) >= 2:
        resolved = [_resolve_one_city(p, known, what) for p in parts]  # first ToolError names its own piece
        spellings = sorted({s for r in resolved for s in r["spellings"]})
        return {"status": "resolved", "asked": word, "spellings": spellings, "matched": "multi"}
    return _resolve_one_city(word, known, what)


def _town_rows(rows, town):
    """The rows that are IN this town (app/data.py:in_towns -> town_of: the posting's own city, the
    clinic's registry town only when the posting has none).

    Not a filter_jobs `city=` parameter, for two reasons the audit measured: filter_jobs matches the
    posting's city OR its clinic's registry town, which answered a question about Ansbach with 53
    postings in Bruckberg, Himmelkron, Obernzenn and Erlangen; and it compares one exact lowercased
    string, which cannot take the several board spellings one town has ('Neuburg an der Donau' and
    'Neuburg/Donau' are 1 and 50 postings in the same town, and a town name may itself contain a comma,
    the separator that parameter splits on)."""
    return [r for r in rows if D.in_towns(r, town["spellings"])] if town else rows


def _job_filters(city="", department="", role_class="", regierungsbezirk="", housing=False, employment_type="", q=""):
    """(the GET /api/jobs query for these arguments, the resolved town or None), with the city and
    department words read the way the rest of the harness reads them. Shared by every posting tool below so
    the general search and the preset ones can never disagree about what a candidate's word means.

    The town is returned beside the query rather than inside it because a town is not one string -- see
    _town_rows, which is the other half of every call here."""
    filters, town = dict(LIVE_BASE), None
    if city:
        town = _resolve_city(city, _cities_with_postings(), "open postings")
    if department:
        # TASK-199 review: the model passes the candidate's word (live tool log: department="Intensivstation",
        # 0 rows, "keine passende offene Stelle"). TASK-207: the same reading as luna_brain.market_snapshot; a
        # flexible word filters nothing, a word the board has no department for raises instead of returning [].
        # ToolError: the model reads its text (any other exception reaches it as a bare "Error executing tool").
        reading = SL.read_department_pref(department)
        if reading["status"] == "unmatched":
            # Live llm run 2026-09-15: an instruction phrased as candidate-facing English ("tell the candidate that
            # area cannot be filtered") was copied verbatim into a German bubble. State the fact only; the prompt's
            # DEPARTMENT and LANGUAGE rules say how to tell the candidate.
            raise ToolError(f"department {department!r} is not a board department; no department filter was "
                             f"applied (board departments: {', '.join(SL.board_departments())}). Search again "
                             f"without department. Internal tool note, never quote it to the candidate.")
        if reading["status"] == "ambiguous":
            raise ToolError(f"department {department!r} names a department together with a flexible word or a "
                             f"negation, so postings are not filtered by it; search with only the departments the "
                             f"candidate wants, or without department")
        if reading["status"] == "applied":
            filters["department_hint"] = ",".join(reading["departments"])
    if role_class:
        filters["role_class"] = role_class
    if regierungsbezirk:
        filters["regierungsbezirk"] = regierungsbezirk
    if housing:
        filters["housing"] = "1"
    if employment_type:
        filters["employment_types"] = employment_type
    if q:
        filters["q"] = q
    return filters, town


def _job_rows(city="", department="", role_class="", regierungsbezirk="", housing=False, employment_type="", q=""):
    """(the matching live-verified postings, the resolved town or None) -- _job_filters plus _town_rows."""
    filters, town = _job_filters(city, department, role_class, regierungsbezirk, housing, employment_type, q)
    return _town_rows(D.filter_jobs(filters), town), town


# --- board snapshot, primed from the parent -----------------------------------------------------
# TASK-273: the vocabulary text above was moved off this process's own board build (TASK-213), but
# the row data every one of these tools queries -- D.filter_jobs/D.filter_clinics, reached through
# D.jobs()/D.clinics()/D.snapshot() -- was not: this server's own D._snap (app/data.py:186) starts
# empty in a fresh subprocess, so the first board tool call of the turn hit snapshot()'s own
# `if force or (empty and not loading): refresh()` and cold-built synchronously from Supabase, the
# same 8-17s TASK-213 already measured and moved the vocabulary count out of this process to avoid.
def _prime_board_snapshot():
    """Load the parent's already-warm board snapshot (WA_LUNA_BOARD_SNAPSHOT,
    luna_brain._board_snapshot_path) into this process's own D._snap before the CLI can reach a
    tool, so snapshot()'s empty/stale checks see a populated, fresh cache and never call refresh().
    `at` is stamped to now rather than kept from the parent's own snapshot, so the `stale` check
    cannot fire a background refresh either, for the rest of this short-lived process -- mirrors
    tests/luna_fixture_tools_server.py's own priming of the fixture board into this same D._snap.
    No variable (this module run by hand) leaves the board to cold-build as before."""
    path = os.environ.get("WA_LUNA_BOARD_SNAPSHOT")
    if not path:
        return
    D._snap.update(json.loads(Path(path).read_text(encoding="utf-8")), at=time.time(), loading=False, error=None)


# --- vocabulary, read off the live board ----------------------------------------------------
# AC1 of TASK-213: the model only ever learned the filters from bare parameter names, so it never used
# them. What it needs is the values a filter takes and how much of the board each covers -- and that
# has to come from the board, because a list typed into this file is wrong the moment a department
# disappears or the housing share moves. The counting itself lives in board_vocabulary.py, because the
# parent process does it (see below).
def vocabulary_for_this_server():
    """The vocabulary lines to write into the tool descriptions.

    Normally the parent process built them from the snapshot it already holds and passed the file
    (WA_LUNA_BOARD_VOCABULARY, luna_brain._mcp_config_path): this server is spawned fresh for every
    single turn, and counting the board here meant a cold Supabase build -- 8-17s measured -- between
    the CLI starting this process and the MCP handshake, against a 30s connect deadline the parent
    cannot see being missed (TASK-213 review). A missing or unreadable file is a bug in the parent and
    raises here rather than serving a board nobody counted; without the variable at all (this module
    run by hand) the board is counted here."""
    path = os.environ.get("WA_LUNA_BOARD_VOCABULARY")
    if not path:
        return vocabulary_lines()
    lines = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = sorted({key for keys in TOOL_VOCABULARY.values() for key in keys} - set(lines))
    if missing:
        raise RuntimeError(f"{path} carries no vocabulary line for {missing}")
    return lines


# Which lines each tool carries: the vocabulary of its own parameters, nothing else.
TOOL_VOCABULARY = {
    "search_postings": ("board", "city", "department", "regierungsbezirk", "role_class", "employment_type",
                        "housing", "childcare"),
    "search_postings_with_housing": ("housing", "city", "department", "regierungsbezirk", "childcare"),
    "list_clinics_with_housing": ("housing", "city", "regierungsbezirk"),
    "list_cities_with_postings": ("department", "housing", "regierungsbezirk"),
    "count_postings": ("city", "department", "regierungsbezirk", "role_class", "employment_type", "housing"),
    "list_clinics": ("city", "regierungsbezirk"),
    "get_posting": ("housing", "childcare"),
    "board_api_get": ("api_columns",),
}


def apply_board_vocabulary():
    """Write the live vocabulary into the registered tools' descriptions -- what the CLI sends the model
    as each tool's schema. Called at server start (serve()), before a single turn can call a tool.

    ``mcp._tool_manager`` is the only handle the MCPServer offers on a registered tool; the public API
    is the async list_tools() view. Idempotent: the base description is captured once, at import. The
    docstring's own line breaks and indentation are squeezed out -- these go to the model on every turn."""
    unknown = sorted(set(TOOL_VOCABULARY) - set(_BASE_DESCRIPTIONS))
    if unknown:
        raise RuntimeError(f"TOOL_VOCABULARY names {unknown}, which are not registered tools")
    lines = vocabulary_for_this_server()
    for name, base in _BASE_DESCRIPTIONS.items():
        mcp._tool_manager.get_tool(name).description = "\n".join(
            [" ".join(base.split()), *(lines[k] for k in TOOL_VOCABULARY.get(name, ()))])
    return lines


# --- board queries ---------------------------------------------------------------------------
@mcp.tool()
def search_postings(city: str = "", department: str = "", role_class: str = "", regierungsbezirk: str = "",
                     housing: bool = False, employment_type: str = "", q: str = "") -> dict:
    """Search open Pflege postings on the board. Same filters as GET /api/jobs. Call this whenever
    the candidate names a city, department or region that market_snapshot did not already cover --
    do not guess or say you have no data when a live search would answer it directly. Every filter the
    candidate stated belongs in the call; for a flat, search_postings_with_housing is the shorter way.
    Returns {shown, total}: at most 5 postings, and how many matched in all. There is no limit argument --
    5 is the most that may ever go into one message. Say the total when it is larger ("und N weitere") and
    offer to narrow the search with a criterion the shown rows actually differ in; never a board link.
    With a city it also returns town.board_spellings -- how the board writes the town you asked for
    ("Lohr am Main" -> 'Lohr a. Main'). Name the town that way. Every posting returned is IN that town by
    its own ad, never one filed there because its clinic's head office is."""
    args = {"city": city, "department": department, "role_class": role_class, "regierungsbezirk": regierungsbezirk,
            "housing": housing, "employment_type": employment_type, "q": q}
    _log_call("search_postings", args)
    rows, town = _job_rows(city, department, role_class, regierungsbezirk, housing, employment_type, q)
    return _listing(rows, town=town)


@mcp.tool()
def search_postings_with_housing(city: str = "", department: str = "", regierungsbezirk: str = "") -> dict:
    """Open postings whose ad says something about Wohnen -- the housing filter is already on. Call this
    the moment the candidate needs an Unterkunft/Wohnung and a place or department is on the table: a
    stated need must be in the call, not only in your reply. Returns {shown, total} like search_postings:
    at most 5 postings and how many matched in all. total=0 means there is nothing with the mark under
    these criteria (say so plainly; list_cities_with_postings(housing=true) gives the real alternatives),
    never a reason to offer a posting without it. READ housing_kind ON EVERY ROW BEFORE YOU PROMISE A
    FLAT: 'accommodation' = the clinic itself offers a room/flat/Wohnheim; 'relocation_support' = it only
    helps look for one or pays towards the move, so say exactly that and never "mit Wohnung";
    'unspecified' = it carries the mark and says neither, so get_posting and read enr_housing_evidence,
    the ad's own words. No row here says anything about price, size or how long you may stay."""
    args = {"city": city, "department": department, "regierungsbezirk": regierungsbezirk}
    _log_call("search_postings_with_housing", args)
    rows, town = _job_rows(city=city, department=department, regierungsbezirk=regierungsbezirk, housing=True)
    return _listing(rows, town=town)


@mcp.tool()
def list_clinics_with_housing(city: str = "", regierungsbezirk: str = "", limit: int = 10) -> list[dict]:
    """The clinics that have at least one open posting whose ad says something about Wohnen, each with how
    many, split into the ones that actually offer somewhere to live (postings_with_accommodation) and the
    ones that only help with the search or the move (postings_with_relocation_support). Call this for a
    clinic-level housing question ("welche Kliniken bieten eine Wohnung") -- and answer it from
    postings_with_accommodation, never from the total."""
    _log_call("list_clinics_with_housing", {"city": city, "regierungsbezirk": regierungsbezirk, "limit": limit})
    rows, _ = _job_rows(city=city, regierungsbezirk=regierungsbezirk, housing=True)
    by_clinic = {}
    for r in rows:
        # clinic_key, not the name: two sites of one group share a name and are two clinics (and the
        # entry's own clinic_id was whichever row came first), while an employer the registry has not
        # linked yet has no clinic_id and is still one clinic. Same key in the generated housing line.
        key = clinic_key(r)
        if not key:
            continue
        entry = by_clinic.setdefault(key, {"clinic_id": r.get("clinic_id"), "clinic_name": _clinic_name(r),
                                           "city": _city(r), "regierungsbezirk": r.get("regierungsbezirk"),
                                           "postings_with_housing": 0, "postings_with_accommodation": 0,
                                           "postings_with_relocation_support": 0})
        entry["postings_with_housing"] += 1
        kind = D.housing_kind(r)
        if kind == "accommodation":
            entry["postings_with_accommodation"] += 1
        elif kind == "relocation_support":
            entry["postings_with_relocation_support"] += 1
    out = sorted(by_clinic.values(), key=lambda e: (-e["postings_with_housing"], e["clinic_name"]))
    return out[:_limit(limit)]


@mcp.tool()
def list_cities_with_postings(department: str = "", housing: bool = False, regierungsbezirk: str = "",
                               limit: int = 15) -> list[dict]:
    """Which cities actually have open postings -- for a department, or with a flat (housing=true), or at
    all -- most postings first. Call this when the candidate is flexible about where, or when their own
    city has nothing for what they need and you would otherwise have to name a town from memory: every
    city here is one the board has postings in right now."""
    args = {"department": department, "housing": housing, "regierungsbezirk": regierungsbezirk, "limit": limit}
    _log_call("list_cities_with_postings", args)
    rows, _ = _job_rows(department=department, regierungsbezirk=regierungsbezirk, housing=housing)
    by_city = {}
    for r in rows:
        city = _city(r)
        if not city:
            continue
        entry = by_city.setdefault(city, {"city": city, "regierungsbezirk": r.get("regierungsbezirk"),
                                          "postings": 0, "clinics": set()})
        entry["postings"] += 1
        if clinic_key(r):
            entry["clinics"].add(clinic_key(r))
    out = [{**e, "clinics": len(e["clinics"])} for e in by_city.values()]
    out.sort(key=lambda e: (-e["postings"], -e["clinics"], e["city"]))
    return out[:_limit(limit, default=15)]


@mcp.tool()
def count_postings(city: str = "", department: str = "", role_class: str = "", regierungsbezirk: str = "",
                    housing: bool = False, employment_type: str = "") -> dict:
    """How many open postings, distinct clinics and cities match these criteria, plus how many of them say
    anything about Wohnen. Call this for a number ("wie viele Stellen haben Sie in X") instead of counting
    rows from a search yourself, and always with at least one filter: the board-wide total is already in
    market_snapshot.open_jobs, so calling this with every parameter empty only re-derives a number you have.
    For a candidate who needs a flat the honest number is with_accommodation -- ads that offer somewhere to
    live; with_relocation_support counts the ones that only help look or pay towards the move, and
    with_housing is the two added together, so never quote it as "Stellen mit Wohnung"."""
    args = {"city": city, "department": department, "role_class": role_class, "regierungsbezirk": regierungsbezirk,
            "housing": housing, "employment_type": employment_type}
    if not any(args.values()):
        # TASK-213 review: live runs kept calling this with every parameter empty to re-derive a number the
        # payload already carries -- two seconds of the turn for nothing, and the prompt rule saying so did
        # not hold across runs (3 runs 2026-09-16, two of them did it). The refusal says where the number is,
        # so the turn just answers; the log keeps the attempt, marked, so a test can see it was not answered.
        _log_call("count_postings", {**args, "refused": "no filter"})
        raise ToolError("count_postings needs at least one filter (city, department, role_class, "
                        "regierungsbezirk, housing, employment_type). The board-wide total is already in "
                        "this turn's market_snapshot.open_jobs -- answer that from the payload, no call.")
    _log_call("count_postings", args)
    filters, town = _job_filters(city, department, role_class, regierungsbezirk, housing, employment_type)
    rows = _town_rows(D.filter_jobs(filters), town)
    kinds = [D.housing_kind(r) for r in rows]
    counted = {"postings": len(rows), "clinics": len({clinic_key(r) for r in rows if clinic_key(r)}),
               "cities": len({_city(r) for r in rows if _city(r)}),
               "with_housing": sum(1 for k in kinds if k is not None),
               "with_accommodation": kinds.count("accommodation"),
               "with_relocation_support": kinds.count("relocation_support"),
               "filters": {k: v for k, v in filters.items() if k not in LIVE_BASE}}
    if town:
        counted["town"] = _town_said(town)
    return counted


@mcp.tool()
def get_posting(posting_id: int) -> dict | None:
    """Everything the board holds about ONE posting (board-public fields only), for an id already seen in a
    search result or market_snapshot: the ad's own text (description), what it asks for (enr_requirements,
    enr_experience, enr_language_req, qualification_hint), what it pays (enr_tariff, enr_pay_grade), the
    housing wording in the ad's own words (enr_housing_evidence, next to housing_kind), plus start_date and
    contract, on top of the fields a search row already carries. Call it before saying anything about a
    posting beyond its clinic, city and department -- a search row carries nothing else, so the rest is
    invention unless it came from here. A field the board has no value for comes back null: that means this
    ad did not say it, never that the answer is no -- say the clinic confirms it. The board records nothing
    at all about shifts, night/weekend work or surcharges, so there is no field to read and no honest answer
    here: for those, say the clinic decides it and offer to ask. null instead of a row = no such posting."""
    _log_call("get_posting", {"posting_id": posting_id})
    row = next((j for j in D.filter_jobs(dict(LIVE_BASE)) if j.get("posting_id") == posting_id), None)
    if row is None:
        # Withheld, not missing, and said so: this used to read the whole open board, so a posting the
        # verifier had found gone came back looking exactly like a live one (TASK-145, Ivan 2026-09-21).
        if any(j.get("posting_id") == posting_id for j in D.jobs()):
            raise ToolError(f"posting {posting_id} is withheld: it is still on the open board, but the verifier "
                            f"no longer confirms it is live, so it must not be named, described or offered. "
                            f"Search again for what the candidate needs. Internal tool note, never quote it.")
        return None
    # The ad's text and the enrichment excerpts are not in the snapshot (app/data.py:JOB_COLS selects
    # v_postings); job_detail is the board's own GET /api/jobs/{id} read of the postings row itself.
    # Redacted like every other door here -- Luna is no member (app/data.py:redact).
    detail = D.job_detail(posting_id)
    if detail is None:
        # `or {}` here degraded the answer to the bare search row, so every POSTING_DETAIL_FIELD
        # came back null -- and this tool's own description tells the model a null field "means this
        # ad did not say it, never that the answer is no". A detail read that failed is then
        # indistinguishable from an ad that genuinely said nothing, and the candidate is told "das
        # steht bei dieser Stelle nicht dabei" about an ad nobody read (TASK-146). Loud, like the
        # withheld branch two lines up.
        raise ToolError(f"posting {posting_id} is in the live board but its detail row could not be "
                        f"read, so the ad's own text and requirements are NOT available -- do not "
                        f"say anything about this posting beyond clinic, city and department, and "
                        f"do not read the missing fields as 'the ad did not say it'. Internal tool "
                        f"note, never quote it to the candidate.")
    full = D.redact([{**row, **detail}], None)[0]
    return {**_job_row(full), **{k: full.get(k) for k in POSTING_DETAIL_FIELDS}}


@mcp.tool()
def list_clinics(city: str = "", regierungsbezirk: str = "", has_jobs: bool = True, limit: int = 10) -> list[dict]:
    """List clinics on the board, same filters as GET /api/clinics. Use this for a clinic-level
    question (which hospitals are in a city/region) rather than a posting-level one; for clinics with a
    flat, list_clinics_with_housing."""
    _log_call("list_clinics", {"city": city, "regierungsbezirk": regierungsbezirk, "has_jobs": has_jobs, "limit": limit})
    filters, town = {}, None
    if city:
        # The candidate's own spelling, against the registry's towns -- filter_clinics compares the town
        # exactly too, so 'Nuernberg' answered [] here just as it did for postings (TASK-145). The town is
        # applied below rather than as filters["city"] for the same reason as _town_rows: one town regularly
        # has several board spellings, and a town name may itself contain the comma that parameter splits on.
        town = _resolve_city(city, _registry_towns(), "clinics")
    if regierungsbezirk:
        filters["regierungsbezirk"] = regierungsbezirk
    if has_jobs:
        filters["has_jobs"] = "1"
    rows = D.filter_clinics(filters)
    if town:
        rows = [c for c in rows if D.town_match_keys(c.get("town") or "")
                & {k for s in town["spellings"] for k in D.town_match_keys(s)}]
    if has_jobs:
        rows = _live_clinics(rows)
    return [{"clinic_id": c.get("clinic_id"), "name": c.get("name"), "town": c.get("town"),
             "regierungsbezirk": c.get("regierungsbezirk"), "beds": c.get("beds")}
            for c in rows[:_limit(limit)]]


# --- the candidate's own CV, against the board ------------------------------------------------
# TASK-145, Ivan 2026-09-21: app/cv.py:match() has ranked postings against a CV since TASK-169, but only
# after consent, on the handover path (app/wa/api.py -> queue.py) -- so the conversation itself could
# never answer "welche davon passt zu meinem Lebenslauf" and fell back to guessing from the chat.
#
# The candidate is the turn's own context, never an argument: this server is spawned per turn and is told
# whose turn it is the same way it is told which database to read (WA_LUNA_PHONE, written by
# luna_brain._mcp_config_path next to WA_SQLITE_PATH). So the number never enters the model's tool call,
# the tool-call log or any result -- and the model cannot rank a CV that is not this conversation's.
CV_PROFILE_KEYS = ("roles", "departments", "qualifications", "cities", "languages", "experience_years")


def _turn_phone():
    phone = (os.environ.get("WA_LUNA_PHONE") or "").strip()
    if not phone:
        raise ToolError("this tools server was started without WA_LUNA_PHONE, so it cannot tell whose CV to "
                        "read (luna_brain._mcp_config_path passes it, like WA_SQLITE_PATH). Answer without "
                        "this tool. Internal tool note, never quote it to the candidate.")
    return phone


def _stored_cv_text(phone):
    """This thread's stored CV text (the card key app/wa/api.py's media intake appends to, TASK-199).

    Read with a plain select rather than store.thread(): that helper creates the thread row on first
    contact ("an unknown number is a lead, not an error"), and nothing this model calls may write."""
    from .. import store as ST

    conn = ST.db()
    try:
        row = conn.execute("select slots from wa_threads where phone=?", (phone,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ToolError("no thread is stored for this conversation's number, so there is no CV to match. "
                        "Internal tool note, never quote it to the candidate.")
    return (json.loads(row["slots"] or "{}").get("cv_text") or "").strip()


@mcp.tool()
def match_cv_to_postings() -> dict:
    """Rank the postings that are open right now against the CV this candidate already sent us, and say why
    each one ranks. Call it for "welche Stelle passt zu mir / zu meinem Lebenslauf", and before naming which
    of several postings fits them: this is the board's own matcher (role, department, town/region, freshness)
    over their actual CV text, not a guess from the chat. Takes no arguments -- it reads this conversation's
    own stored CV. Returns {shown, total, withheld_not_live, profile}: at most 5 postings, each with a score
    and the reasons it scored, how many matched in all, and the profile read out of the CV so you can check
    it (say what it got wrong if the candidate contradicts it). Reads only -- it sends nothing, applies to
    nothing and tells no clinic anything. An error means no CV is stored yet: ask them to send one."""
    _log_call("match_cv_to_postings", {})       # no argument, and deliberately no number in the log
    from ... import cv as CV

    cv_text = _stored_cv_text(_turn_phone())
    if not cv_text:
        raise ToolError("this candidate has no CV text stored yet (none sent, or the upload had no readable "
                        "text), so nothing can be ranked. Ask them for the Lebenslauf. Internal tool note.")
    profile = CV.profile_from_text(cv_text)
    # limit = the whole open board: app/cv.py:match() would otherwise cut its own ranking at 50 and the
    # total below would be that cut, not the truth. The cut that does apply is LISTING_LIMIT, in _listing.
    ranked = CV.match(profile, limit=len(D.jobs()) or 1)
    live = [r for r in ranked if r.get("verify_status") == LIVE_BASE["verify"]]
    return {**_listing(live, project=lambda r: {**_job_row(r), "score": r["score"], "why": r["why"]}),
            # match() ranks the whole open board; a posting the verifier no longer confirms is dropped here
            # and said, not silently swallowed (TASK-145).
            "withheld_not_live": len(ranked) - len(live),
            "profile": {k: profile.get(k) for k in CV_PROFILE_KEYS}}


@mcp.tool()
def get_clinic_contact(clinic_id: str) -> dict | None:
    """The best-known Pflegedirektion/HR contact e-mail for one clinic, or null if none is known
    yet. Only ever name a contact this tool actually returned -- never invent or guess an address."""
    _log_call("get_clinic_contact", {"clinic_id": clinic_id})
    if CT is None:
        return None
    conn = CT.db()   # not app.wa.store.db() directly -- CT.db() also applies contacts.py's own SCHEMA
    try:
        return CT.get_contact(conn, str(clinic_id))
    finally:
        conn.close()


# --- fallback: the board's own docs, and the public board API ---------------------------------
# For the question the preset tools do not cover. Both doors are read-only and reach nothing but the
# public board: no ops route, no write route, no database, no file outside the four documents below.
BOARD_DOCS = {
    "skill": "skill/SKILL.md",
    "api": "skill/references/api.md",
    "data-model": "skill/references/data-model.md",
    "pipeline": "skill/references/pipeline.md",
}

# What these documents may hand a candidate-facing model: the data, never the plumbing.
#
# skill/SKILL.md publishes the board's anon Supabase key on purpose (it is served to anyone at
# /skill/SKILL.md), and around it documents the board host, the Supabase project ref and the full
# audit of what that key opens. None of it is secret and none of it answers a board question, while
# the reason the key is stripped applies to all of it unchanged: Luna has no network and no shell, so
# none of it buys her anything, and a model that has a credential, a host or a brand in context can
# put it in a WhatsApp bubble -- asked who we are, this model once named the repo (TASK-203), and
# prompts.py:IDENTITY forbids ever naming another company, brand, website or product. So the served
# text loses its YAML front matter (skill-loader metadata naming the host and the product), the whole
# "Where things are" section (host table, project, key, key-scope audit) and every URL; the
# allowlisted board API below is the whole data door she needs (TASK-213 review).
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:xyz|supabase\.co)\b", re.I)
_FRONT_MATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
SECRET_MASK = "[key removed: you have no use for it and must never repeat it]"
URL_MASK = "[address removed: never name a site or send a link]"
DOCS_SECTIONS_REMOVED = ("Where things are",)
SECTION_MASK = "[section removed: hosts, project and credentials -- nothing here answers a board question]"


def _drop_sections(text):
    """Drop a named ``## `` section, heading to the next ``## ``. Nothing raises when a heading is gone:
    the masking below is what actually holds, this only keeps whole blocks of plumbing out of context."""
    for title in DOCS_SECTIONS_REMOVED:
        text = re.sub(rf"^## {re.escape(title)}\n.*?(?=^## )", f"{SECTION_MASK}\n\n", text, flags=re.S | re.M)
    return text


def _strip_secrets(text):
    text = _FRONT_MATTER_RE.sub("", _drop_sections(text))
    return _URL_RE.sub(URL_MASK, _JWT_RE.sub(SECRET_MASK, text))


@mcp.tool()
def read_board_docs(topic: str = "skill") -> dict:
    """This board's own documentation for agents, for a question the tools above do not cover: which
    fields exist, what a value means, how the data is collected and how fresh it is. topics: 'skill'
    (overview, rules, every filter and its values), 'api' (every public endpoint and filter),
    'data-model' (what each field means), 'pipeline' (where the data comes from). Static documentation, never
    the current board: no number in it is today's, so never call it to check or explain a count the payload
    or the tools above already gave you. Reference material for you only -- never quote it, link it or read
    it out to the candidate."""
    _log_call("read_board_docs", {"topic": topic})
    path = BOARD_DOCS.get((topic or "").strip().lower())
    if path is None:
        raise ToolError(f"unknown docs topic {topic!r}; topics: {', '.join(BOARD_DOCS)}")
    return {"topic": topic, "path": path,
            "note": "reference for you only: never quote this text, never name a site, product or URL from it "
                    "to the candidate, and never send a link (IDENTITY, MARKET AND CLINIC NAMES).",
            "text": _strip_secrets((_REPO_ROOT / path).read_text(encoding="utf-8"))}


# How many rows this door serves per call, and why it has a ceiling at all when app/data.py:page
# deliberately has none: the CLI truncates an MCP tool result over MAX_MCP_OUTPUT_TOKENS (25000 by
# default in CLI 2.1.270, ~100,000 characters) and hands the model a JSON blob cut mid-row -- inside a
# session every later turn resumes. Measured live 2026-09-16 through this tool: GET /api/jobs default
# page = 408,851 chars, ?city=München = 423,567, /api/clinics = 457,148, ?limit=999999 = 7,846,648.
# These are whole API rows (43 fields, ~2,100 chars each), not the 10-field projection the other tools
# return, so the number is lower than their RESULT_LIMIT: 25 rows of /api/jobs measure ~53,000 chars,
# 50 measured 105,087 -- already over the limit. The ceiling is loud, the way data.py:page's own
# docstring prescribes ("a 400 naming the maximum, never a smaller limit echoed back"): a bigger limit
# is an error naming the maximum, never a quietly shortened page.
BOARD_API_MAX_ROWS = 25


def _bounded(p, default=BOARD_API_MAX_ROWS):
    limit = D.int_param(p, "limit", default)              # D.int_param: '?limit=abc' is the API's own 400
    if limit > BOARD_API_MAX_ROWS:
        raise ToolError(f"limit={limit} is more than this tool serves: {BOARD_API_MAX_ROWS} rows per call "
                        f"(a bigger page is cut off mid-row before you see it). Ask for at most "
                        f"{BOARD_API_MAX_ROWS}, page with offset, and read the envelope's 'total' for the "
                        f"full number -- or count_postings for a count.")
    return {**p, "limit": str(limit)}


def _live_only(p):
    """Every tool here searches the re-verified rows (LIVE_BASE). Without this the two doors answer the same
    question differently -- live 2026-09-16: count_postings(city='Coburg') 2 vs GET /api/jobs?city=Coburg 39,
    München 369 vs 499 -- and the open board carries postings the verifier never confirmed or found
    error/blocked/gone.

    TASK-213 left an explicit ``verify=`` in the query as a deliberate way past that base. Ivan removed it on
    2026-09-21 (TASK-145): a posting whose liveness is not confirmed may not reach a candidate-facing model at
    all, and one escape hatch in one door is the whole guarantee gone. Not validated, not narrowed to the
    'safe' values -- refused, so the model reads why instead of silently getting a different board."""
    if p.get("verify"):
        raise ToolError(f"verify={p['verify']!r} is not yours to set: this door serves only the postings the "
                        f"verifier confirmed are still live, exactly like every other tool here. Ask again "
                        f"without verify=. Internal tool note, never quote it to the candidate.")
    return {**p, "verify": LIVE_BASE["verify"]}


def _withheld_not_live(p, live_rows, town=None):
    """How many postings this query matched on the open board but the verifier no longer confirms. Said in
    the envelope so a filtered result is never indistinguishable from a small one (TASK-145)."""
    open_rows = _town_rows(D.filter_jobs({k: v for k, v in p.items() if k != "verify"}), town)
    return len(open_rows) - len(live_rows)


def _api_jobs(p):
    p, town = _bounded(_live_only(p)), None
    if p.get("city"):
        # The same reading as every preset tool (_job_filters): otherwise this door is the way around the
        # city resolution, and 'city=Nuernberg' is total=0 again -- "nothing open there" (TASK-145). The
        # town leaves the query and is applied to the rows, exactly as in _town_rows.
        town = _resolve_city(p["city"], _cities_with_postings(), "open postings")
        p = {k: v for k, v in p.items() if k != "city"}
    rows = _town_rows(D.filter_jobs(p), town)
    out = D.page(rows, p, BOARD_API_MAX_ROWS)
    out["rows"] = D.redact(out["rows"], None)
    out["withheld_not_live"] = _withheld_not_live(p, rows, town)
    if town:
        out["town"] = _town_said(town)
    return out


def _api_clinics(p):
    p, town = _bounded(p), None
    if p.get("city"):
        town = _resolve_city(p["city"], _registry_towns(), "clinics")
        p = {k: v for k, v in p.items() if k != "city"}
    rows = D.filter_clinics(p)
    if town:
        keys = {k for s in town["spellings"] for k in D.town_match_keys(s)}
        rows = [c for c in rows if D.town_match_keys(c.get("town") or "") & keys]
    if p.get("has_jobs"):
        # Same rule as list_clinics: has_jobs on this door counted jobs_open too (TASK-146).
        rows = _live_clinics(rows)
    out = D.page(rows, p, BOARD_API_MAX_ROWS)
    out["rows"] = D.redact(out["rows"], None)
    if town:
        out["town"] = _town_said(town)
    return out


def _api_clinic(clinic_id, p):
    c = D.clinic(clinic_id)
    if not c:
        raise ToolError(f"unknown clinic {clinic_id!r}")
    filters = _live_only({"clinic_id": clinic_id, "sort": "-first_published"})
    jobs = D.filter_jobs(filters)
    # `runs` is owner-only on the app API itself (app/main.py:api_clinic) -- an anonymous caller gets the
    # empty list there and gets it here. `jobs` is the route's own unpaged list, bounded like every other
    # result here, with the full number next to it so a cut list can never read as the whole one.
    return {**c, "jobs_total": len(jobs), "jobs": D.redact(jobs[:BOARD_API_MAX_ROWS], None), "runs": [],
            "jobs_withheld_not_live": _withheld_not_live(filters, jobs)}


# Public, read-only, snapshot-backed board paths only. Everything else -- ops reads, every write, the
# WhatsApp routes, /api/stats (whose last-crawl block reads the crawl-run database rather than the
# board snapshot; the open-jobs total is already in market_snapshot) -- is off this list on purpose.
BOARD_API_PATHS = {
    "/api/jobs": _api_jobs,
    "/api/clinics": _api_clinics,
    "/api/cities": lambda p: D.cities(p.get("q")),
    "/api/facets": lambda p: D.snapshot()["facets"],
    "/api/taxonomy": lambda p: D.taxonomy(),
    # D.int_param, not int(): '?limit=abc' is the app API's own 400 here too, caught below as a ToolError.
    # 15 is the route's own default; _bounded rejects anything above the maximum instead of trimming it.
    "/api/search": lambda p: SE.search(p.get("q", ""), limit=D.int_param(_bounded(p, 15), "limit")),
}
BOARD_API_CLINIC_PREFIX = "/api/clinics/"

# Every key any board row can carry a URL in. TASK-151: ``_job_row`` has kept links out of the
# PRESET tools since TASK-146, and app/wa/luna/offer.py keeps them out of the payload -- but
# board_api_get handed the raw board rows over, so ``/api/jobs`` gave the model ``external_url`` and
# ``/api/clinics`` gave it ``website``/``careers_url``/``board``. prompts.py tells the model to use
# that tool, so "the model is never given a posting URL in any payload" (TASK-150 AC#3) was false in
# production on every thread. Stripped at the door below rather than in each handler, so a path
# added later cannot reopen it.
URL_FIELDS = ("external_url", "source_url", "website", "careers_url", "board", "url", "link",
              "apply_url", "ats_url")


def _without_urls(value):
    """``value`` with every URL-bearing key removed, however deep it sits (a clinic's ``jobs`` list,
    a posting's ``observations``)."""
    if isinstance(value, dict):
        return {k: _without_urls(v) for k, v in value.items() if k not in URL_FIELDS}
    if isinstance(value, list):
        return [_without_urls(v) for v in value]
    return value


@mcp.tool()
def board_api_get(path: str, query: str = "") -> dict | list:
    """One read-only GET against the public board API, for a question the tools above do not cover (a
    filter they do not preset, a facet list, a fuzzy name search). path is one of: /api/jobs,
    /api/clinics, /api/clinics/{clinic_id}, /api/cities, /api/facets, /api/taxonomy, /api/search. query is
    the query string ('city=Coburg&housing=1&limit=5'); read_board_docs('api') documents every filter and
    the {total, limit, offset, next_offset, rows} envelope -- 'total' is the full number of matches however
    few rows come back, and 'withheld_not_live' is how many more matched on the open board but are not
    confirmed live. Exactly the same rows as the tools above (the re-verified postings): verify= is not a
    filter you may set, it is an error. At most 25 rows per call, a bigger limit is an error, so ignore what
    the docs say about limit=999999 -- and at most 5 of them may ever go into a message to the candidate
    (search_postings gives you those five and the true total directly). Read-only public board data: any
    other path is an error, personal data (contact e-mail addresses) is removed, nothing here can change
    anything, and no row carries a URL at all -- there is no link here for you to pass on."""
    _log_call("board_api_get", {"path": path, "query": query})
    path = (path or "").strip()
    params = dict(parse_qsl(str(query or "").lstrip("?")))
    handler, args = BOARD_API_PATHS.get(path), ()
    if handler is None and path.startswith(BOARD_API_CLINIC_PREFIX):
        clinic_id = path[len(BOARD_API_CLINIC_PREFIX):]
        if clinic_id and "/" not in clinic_id:
            handler, args = _api_clinic, (clinic_id,)
    if handler is None:
        raise ToolError(f"path {path!r} is not a board API path you may call; allowed: "
                         f"{', '.join([*BOARD_API_PATHS, BOARD_API_CLINIC_PREFIX + '{clinic_id}'])}. Read-only "
                         f"public board data only -- no other route is reachable from here; one posting is "
                         f"get_posting(posting_id).")
    try:
        return _without_urls(handler(*args, params))
    except HTTPException as exc:      # the app API's own 400s (a non-integer limit, a negative offset)
        raise ToolError(f"GET {path} rejected the query {query!r}: {exc.detail}")


# --- show_clinic_photos (TASK-131 round 7, Ivan 2026-09-23): the ONE tool in this server that sends
# something, rather than only reading -----------------------------------------------------------
#
# UNLIKE every tool above, this one is not answered from the in-process snapshot: the photo/blurb
# data (clinic_photos/clinic_blurbs, app/runs.py) lives wherever the board's own crawler deployment
# writes it, confirmed live 2026-09-23 to differ from this machine's own data/app.sqlite (that file
# has neither table yet). GET https://pflege-board.exe.xyz/api/clinics/{id}/expose is the same
# public, unauthenticated route a browser would hit -- reached the ordinary way, over HTTP, not
# in-process the way board_api_get's own paths are.
BOARD_PUBLIC_BASE = "https://pflege-board.exe.xyz"
#: Where a downloaded photo is staged on the mini before bridge.Client.send_gallery can reach it --
#: send_gallery's own contract (bridge/adb_driver.py) is a path already on the MINI's filesystem,
#: never bytes over the HTTP call itself. Kept apart from this session's own manual test staging
#: (~/wa_outbound_test) so a live tool call and a human poking at the rail by hand never collide.
#: TASK-272: read from config (WA_LUNA_MEDIA_HOST/WA_LUNA_MEDIA_DIR) rather than hardcoded here, so
#: config.readiness() can show what this will actually ssh/scp to.
MINI_HOST = C.LUNA_MEDIA_HOST
MINI_MEDIA_DIR = C.LUNA_MEDIA_DIR


def _fetch_clinic_expose(clinic_id):
    url = f"{BOARD_PUBLIC_BASE}/api/clinics/{clinic_id}/expose"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ToolError(f"unknown clinic {clinic_id!r} -- the board has no such clinic_id. "
                            f"Internal tool note, never quote it to the candidate.") from exc
        raise ToolError(f"the board's own expose endpoint answered HTTP {exc.code} for "
                        f"clinic {clinic_id!r} -- nothing was sent. Internal tool note.") from exc
    except urllib.error.URLError as exc:
        raise ToolError(f"could not reach the board to look up clinic {clinic_id!r} ({exc.reason}) -- "
                        f"nothing was sent. Internal tool note.") from exc


def _download_to_temp(url_path, suffix):
    url = f"{BOARD_PUBLIC_BASE}{url_path}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise ToolError(f"could not download {url_path!r} ({exc}) -- nothing was sent. "
                        f"Internal tool note.") from exc
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="clinic_photo_")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def _staged_media_name(clinic_id, url_path, suffix):
    """-> the remote basename one (clinic_id, url_path) pair always stages under (TASK-272).
    Hashes the clinic/URL pair, not the downloaded bytes: a content hash needs the bytes in hand
    already, which defeats the point of checking before downloading them at all. mkstemp's random
    basename (the previous scheme) guaranteed the opposite of this -- a fresh name, and therefore a
    fresh download and scp, for every candidate who reaches the same clinic."""
    digest = hashlib.sha1(f"{clinic_id}:{url_path}".encode("utf-8")).hexdigest()
    return f"{digest}{suffix}"


def _staged_already(remote_name):
    """-> True if MINI_MEDIA_DIR/remote_name is already sitting on the mini. An ssh test, not a
    local stat -- this process and MINI_MEDIA_DIR are on different machines. Any non-zero exit
    (the file is genuinely absent, or ssh itself failed) reads as "not confirmed staged": the
    caller falls back to downloading and staging exactly as it did before this existence check
    existed, so an unreachable mini still fails loudly at that step, not silently here."""
    check = subprocess.run(["ssh", MINI_HOST, f"test -f {MINI_MEDIA_DIR}/{remote_name}"],
                           capture_output=True, text=True, timeout=20)
    return check.returncode == 0


def _stage_on_mini(local_path, remote_name):
    """scp local_path to MINI_MEDIA_DIR/remote_name, ssh mkdir -p first -- the same two-step this
    session's own manual staging has used all night, just from code instead of a human's shell.
    -> the remote path send_gallery/send_document expects. Raises ToolError, never partial state
    the caller has to notice on its own, if either step fails."""
    remote = f"{MINI_MEDIA_DIR}/{remote_name}"
    mkdir = subprocess.run(["ssh", MINI_HOST, f"mkdir -p {MINI_MEDIA_DIR}"],
                           capture_output=True, text=True, timeout=20)
    if mkdir.returncode != 0:
        raise ToolError(f"could not prepare the phone rail's staging directory ({mkdir.stderr.strip()[:200]}) "
                        f"-- nothing was sent. Internal tool note.")
    scp = subprocess.run(["scp", local_path, f"{MINI_HOST}:{remote}"],
                         capture_output=True, text=True, timeout=30)
    if scp.returncode != 0:
        raise ToolError(f"could not stage the photo on the phone rail ({scp.stderr.strip()[:200]}) -- "
                        f"nothing was sent. Internal tool note.")
    return remote


@mcp.tool()
def show_clinic_photos(clinic_id: str) -> dict:
    """Send this candidate a short visual presentation of ONE clinic -- its real photo(s), captioned
    with a short researched paragraph about it, as one WhatsApp message -- at the moment their own
    search has genuinely narrowed to this clinic (a city was named and this is one of the clinics
    that matches), BEFORE asking for documents. Ivan, 2026-09-23: the funnel's climax moment (a
    real, specific clinic identified) was underserved by a bare vacancy count; this gives the
    candidate something real to react to right when interest peaks, instead of only after.

    Call it AT MOST ONCE per clinic per conversation -- calling it again for the same clinic re-sends
    the same photo, which reads as a mistake, not enthusiasm. Skip it entirely for a clinic this
    tool already reports nothing available for (do not retry hoping the data appeared mid-turn).

    ONLY sends when there is at least one photo -- {"sent": true, "photos": N} -- in which case do
    NOT also describe the photo yourself in a bubble; the message already carries the researched
    paragraph as its caption. The bubble you write AFTER calling this is mandatory, not optional
    small talk -- it must end with one concrete, specific question that carries the candidate to
    the next step (an Urkunde, a start date, a shift preference), never a bare acknowledgement.

    When this clinic has a researched paragraph but no photo yet, nothing is sent by the tool --
    {"sent": false, "presentation_text": "..."} -- and you write it into your OWN reply, SHORTENED to
    what actually sells this clinic (drop enumerations and secondary detail), then close with the
    same kind of leading question, since there is no photo message for it to ride along with.

    {"sent": false, "reason": "..."} with no presentation_text means there is nothing at all yet for
    this clinic (both photos and the researched paragraph are still empty -- the collection
    pipelines are mid-rollout, TASK-223): continue in text as normal, never claiming to have shown
    something you have not."""
    _log_call("show_clinic_photos", {"clinic_id": clinic_id})   # no phone number in the log
    phone = _turn_phone()
    data = _fetch_clinic_expose(clinic_id)
    photo_paths = data.get("photos") or []
    presentation = data.get("presentation") or {}
    caption = (presentation.get("text_de") or "").strip()
    if os.environ.get("WA_LUNA_NO_SEND"):
        # A dry run (app/wa/luna/shadow_run.py). The lookup above still runs, because WHICH branch
        # this tool takes is exactly what the report exists to show -- only the handset is spared.
        if not photo_paths:
            return ({"sent": False, "presentation_text": caption, "dry_run": True} if caption else
                    {"sent": False, "reason": "no photos or presentation available yet for this clinic",
                     "dry_run": True})
        return {"sent": True, "photos": len(photo_paths[:5]), "has_presentation": bool(caption),
                "dry_run": True}
    if not photo_paths:
        if caption:
            return {"sent": False, "presentation_text": caption}
        return {"sent": False, "reason": "no photos or presentation available yet for this clinic"}

    # TASK-250: every other outbound goes through api._send, which stops here (C.AUTOSEND) and here
    # (the thread's pinned rail) before anything leaves the process -- this tool called BR.Client()
    # unconditionally instead, so a staging deployment with AUTOSEND unset sent real photos anyway,
    # and a thread already pinned to the meta rail got them from the handset number, a stranger to
    # that candidate. Same two gates, applied here.
    from .. import store as ST
    from .. import transport as T
    if not C.AUTOSEND:
        return {"sent": False, "reason": "AUTOSEND is off"}
    rail = T.rail_for(phone=phone)
    if rail != "bridge":
        return {"sent": False, "reason": f"this thread is pinned to the {rail} rail, not the phone "
                "rail show_clinic_photos sends from"}

    # TASK-271: this tool's own docstring says "call it AT MOST ONCE per clinic per conversation",
    # but that line is read by the model, not enforced -- a retry the model never sees (the turn's
    # `claude -p` killed on LUNA_TIMEOUT_SEC, or the ~3-minute catch-up re-driving the same inbound)
    # reaches this far again with no memory of the first call. send_gallery has no idempotency key
    # of its own (its docstring: "MECHANISM PROOF, NOT PRODUCTION-READY ... calling it twice sends
    # the photos twice") and bridge/executor.py never puts gallery/photos/document sends through the
    # ledger's client_msg_id replay guard the way a text send is. The wa_messages row this same call
    # writes on success below (meta.action="show_clinic_photos") is the one mechanical fact a repeat
    # call can check before it touches the network or the handset a second time.
    conn = ST.db()
    try:
        already_sent = any(r["meta"].get("action") == "show_clinic_photos"
                           and r["meta"].get("clinic_id") == clinic_id
                           for r in ST.messages_for(conn, phone, direction="out"))
    finally:
        conn.close()
    if already_sent:
        return {"sent": False, "reason": "already sent to this candidate"}

    # TASK-251: check_reply's LINK gate (grounding.has_link, TASK-144) runs on the model's own
    # bubbles only -- this caption is sent straight from here and never becomes one, so a researched
    # blurb carrying the clinic's own site went out uncensored. Same rule, same fallback the no-photo
    # branch above already uses: the model gets presentation_text and writes it in its own words,
    # where LINK applies.
    from . import grounding as GR
    if GR.has_link(caption):
        return {"sent": False, "presentation_text": caption}

    local_files, remote_files = [], []
    try:
        for p in photo_paths[:5]:
            suffix = os.path.splitext(p)[1] or ".jpg"
            # TASK-272: a deterministic name keyed on (clinic_id, p) lets a second candidate
            # reaching this same clinic reuse what an earlier one already staged, instead of a
            # fresh mkstemp-random name forcing a fresh board download and scp every single time.
            remote_name = _staged_media_name(clinic_id, p, suffix)
            if _staged_already(remote_name):
                remote_files.append(f"{MINI_MEDIA_DIR}/{remote_name}")
                continue
            local = _download_to_temp(p, suffix)
            local_files.append(local)
            remote_files.append(_stage_on_mini(local, remote_name))
        try:
            BR.Client().send_gallery(phone, remote_files, caption=caption)
            # api._send writes a wa_messages row per bubble it sends so outbound_since_last_turn
            # (prompts.py) can show a resumed session what already went out; this call bypassed
            # that too, so a next turn resuming without CLI session memory of this tool call read
            # the candidate's reaction to the photos as an answer to whatever text preceded them.
            conn = ST.db()
            try:
                ST.record_outbound(conn, phone, None, caption, kind="gallery",
                                   meta={"action": "show_clinic_photos", "clinic_id": clinic_id,
                                         "photos": len(remote_files)})
            finally:
                conn.close()
        except BR.BridgeError as exc:
            # Same "was the handset actually touched" distinction tools/wa_bridge.py draws for a
            # human operator (bridge/errors.py's HANDSET_TOUCHED_CODES) -- an uncaught BridgeError
            # here would otherwise crash this whole tools server mid-turn, which loses every OTHER
            # tool call already made this turn along with it.
            #
            # CODE_ANSWER_TIMEOUT belongs in this branch too (TASK-247): OPS_PATH's own contract
            # (bridge.py:100-104) is that send_gallery only ever reaches that code after the
            # executor already answered 200 {"state": "queued"} -- this call WAS queued for the
            # handset, so "nothing was sent" is never true of it, and the send is quite possibly
            # still landing while this exception is raised (GALLERY_BUDGET_SEC alone is 187s,
            # more than double the bare client floor this used to wait on).
            if exc.code in BR.HANDSET_TOUCHED_CODES or exc.code == BR.CODE_ANSWER_TIMEOUT:
                raise ToolError(
                    f"the phone rail touched the handset trying to send this but could not confirm "
                    f"it went out (code {exc.code!r}) -- do not tell the candidate photos are coming; "
                    f"say nothing about it and continue normally. Internal tool note.") from exc
            raise ToolError(f"the phone rail refused this send (code {exc.code!r}): {exc} -- nothing "
                            f"was sent. Internal tool note.") from exc
    finally:
        for f in local_files:
            try:
                os.unlink(f)
            except OSError:
                pass
    return {"sent": True, "photos": len(remote_files), "has_presentation": bool(caption)}


# --- read_history (TASK-290, Ivan 2026-09-24): the DB's own paged history, replacing the abandoned
# live-screen-read design (TASK-289: the screen and the DB were found drifted apart twice in one
# night, so the brain must never read the live handset again). Everything here goes through
# store.py's default deleted_at filter -- a forgotten bubble or document is invisible here exactly
# like it already is to turn_context.
_HISTORY_PAGE_MAX = 50


def _history_limit(value):
    return max(1, min(int(value or 20), _HISTORY_PAGE_MAX))


@mcp.tool()
def read_history(before_id: int | None = None, limit: int = 20) -> dict:
    """Page this conversation's own history further back than the recent tail already in your
    context -- call it when the candidate refers to something from an earlier day (yesterday, the
    day before) that the current turn's context does not cover. Never call it to double-check
    something already in front of you; the stored tail is already correct and current for that.

    Returns messages oldest-first within the page, plus every stored attachment's metadata (id,
    document_type, filename, received_at) for this phone. Call it again with before_id set to this
    call's own "oldest_id" to keep paging further back; has_more tells you whether anything older
    is left. A message or attachment marked forgotten never appears here, the same as everywhere
    else -- treat that as if it was never sent, never mention a gap or a deletion."""
    _log_call("read_history", {"before_id": before_id, "limit": limit})
    from .. import store as ST

    phone = _turn_phone()
    conn = ST.db()
    try:
        messages, has_more = ST.messages_before(conn, phone, before_id=before_id,
                                                limit=_history_limit(limit))
        documents = ST.documents_for(conn, phone)
    finally:
        conn.close()
    return {"messages": [{"id": m["id"], "direction": m["direction"], "body": m["body"], "at": m["at"]}
                         for m in messages],
            "has_more": has_more,
            "oldest_id": messages[0]["id"] if messages else None,
            "documents": [{"id": d["id"], "document_type": d.get("document_type"),
                          "original_filename": d.get("original_filename"), "received_at": d["received_at"]}
                         for d in documents]}


@mcp.tool()
def read_document(document_id: int) -> dict:
    """Open one attachment listed by read_history's "documents" and read its extracted text -- the
    listing only gives you id/type/filename, this is the "open and read" step for a specific one of
    them. Raises when the id is unknown, belongs to a different phone, or was forgotten (TASK-289):
    treat that exactly like the attachment was never sent, never mention a deletion or a gap."""
    _log_call("read_document", {"document_id": document_id})
    from .. import store as ST

    phone = _turn_phone()
    conn = ST.db()
    try:
        doc = ST.document_by_id(conn, document_id)
    finally:
        conn.close()
    if doc is None or doc.get("phone") != phone:
        raise ToolError(f"no attachment {document_id!r} is on file for this conversation (unknown id, "
                        f"a different phone's document, or it was forgotten). Internal tool note.")
    return {"document_type": doc.get("document_type"), "original_filename": doc.get("original_filename"),
            "received_at": doc["received_at"], "text": (doc.get("text") or "").strip()}


# --- CV edit assist (TASK-291, Ivan 2026-09-24): find the stored CV, let the model write an updated
# body from what the candidate asked to change, and send it back as a document -- the send half
# follows the same staged-artifact shape show_clinic_photos already proved (stage on the mini, send,
# record the outbound, never crash the tools server on a bridge failure).

def _latest_cv_document(conn, phone):
    """The most recently received non-deleted wa_documents row classified as a CV for this phone,
    or None. Reads wa_documents directly (not the card's cached cv_text) so a document an operator
    has since forgotten (TASK-289) is never found here either, even if the card still holds
    whatever text was extracted from it before that."""
    from .. import store as ST

    docs = [d for d in ST.documents_for(conn, phone) if d.get("document_type") == "lebenslauf"]
    return docs[-1] if docs else None


@mcp.tool()
def find_stored_cv() -> dict:
    """The text of this candidate's own stored CV (Lebenslauf), read from the original file they
    sent -- not a summary. Call it when they ask you to help change or update their CV, before
    writing anything: you need the actual current text to edit from, not what the conversation
    happens to say about it. Raises when none is on file (none ever sent, or the one they sent was
    since forgotten) -- ask them to send it first rather than inventing one."""
    _log_call("find_stored_cv", {})
    from .. import store as ST

    phone = _turn_phone()
    conn = ST.db()
    try:
        doc = _latest_cv_document(conn, phone)
    finally:
        conn.close()
    if doc is None:
        raise ToolError("no CV is on file for this candidate (none sent, or it was forgotten) -- "
                        "ask them to send one before trying to edit it. Internal tool note.")
    text = (doc.get("text") or "").strip()
    if not text:
        raise ToolError("a CV file is on file but it has no extracted text to edit -- ask them to "
                        "resend it. Internal tool note.")
    return {"cv_text": text, "received_at": doc["received_at"]}


@mcp.tool()
def send_updated_cv(cv_text: str) -> dict:
    """Send this candidate an updated version of their CV as a WhatsApp document. Call this only
    after find_stored_cv(), and only with the FULL updated CV body -- every section, not a diff or
    a summary -- incorporating just the change(s) they asked for and never inventing qualifications
    or experience that were not already in their CV or stated by them in this thread. Never call
    this unless the candidate explicitly asked to change or update their CV in this conversation.

    -> {"sent": true} once delivered and recorded, the same as any other outbound send on this
    rail. A short bubble AFTER calling this, confirming what changed, is expected and normal."""
    _log_call("send_updated_cv", {"chars": len(cv_text or "")})
    phone = _turn_phone()
    text = (cv_text or "").strip()
    if not text:
        raise ToolError("cv_text is empty -- nothing to send. Internal tool note.")
    if os.environ.get("WA_LUNA_NO_SEND"):
        return {"sent": False, "dry_run": True}

    from .. import store as ST
    from .. import transport as T
    if not C.AUTOSEND:
        return {"sent": False, "reason": "AUTOSEND is off"}
    rail = T.rail_for(phone=phone)
    if rail != "bridge":
        return {"sent": False, "reason": f"this thread is pinned to the {rail} rail, not the phone "
                "rail send_updated_cv sends from"}

    fd, local_path = tempfile.mkstemp(suffix=".txt", prefix="updated_cv_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        remote_name = f"cv_{hashlib.sha1(f'{phone}:{time.time()}'.encode()).hexdigest()}.txt"
        remote_path = _stage_on_mini(local_path, remote_name)
        try:
            BR.Client().send_document(phone, remote_path)
        except BR.BridgeError as exc:
            # Same "was the handset actually touched" distinction show_clinic_photos already draws.
            if exc.code in BR.HANDSET_TOUCHED_CODES or exc.code == BR.CODE_ANSWER_TIMEOUT:
                raise ToolError(
                    f"the phone rail touched the handset trying to send this but could not confirm "
                    f"it went out (code {exc.code!r}) -- do not tell the candidate an update is "
                    f"coming; say nothing about it and continue normally. Internal tool note.") from exc
            raise ToolError(f"the phone rail refused this send (code {exc.code!r}): {exc} -- nothing "
                            f"was sent. Internal tool note.") from exc
        conn = ST.db()
        try:
            ST.record_outbound(conn, phone, None, "[updated CV]", kind="document",
                               meta={"action": "send_updated_cv", "chars": len(text)})
        finally:
            conn.close()
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass
    return {"sent": True}


# Captured before any vocabulary is appended, so apply_board_vocabulary() is idempotent (the fixture
# tools server in tests/ imports this module and applies it again with its own board).
_BASE_DESCRIPTIONS = {name: tool.description for name, tool in mcp._tool_manager._tools.items()}


def _stamp_ready():
    """Tell the parent the CLI actually received the board tool schemas.

    Fired from the ``list_tools`` hook armed by ``_arm_ready_stamp`` (TASK-274), not unconditionally
    at start: a tools server that dies at start, or that the CLI drops for missing its connect
    deadline, is invisible from outside -- ``claude -p`` still exits 0 with ``is_error`` false and a
    normal-looking reply, and the result envelope carries no MCP server status at all (probed, CLI
    2.1.270) -- so the turn runs with a system prompt that says "TOOLS (mandatory, not optional)",
    names nine tools that are not there, and answers about the board from nothing. Stamping here
    rather than in serve() means a run that never gets as far as a ``tools/list`` response -- the
    stdio loop dying or the CLI dropping it before the handshake completes -- leaves no stamp, where
    stamping unconditionally in serve() would have left one anyway (the process having started is not
    the CLI having received anything). luna_brain._live_reply raises when this file is missing after
    the run. No variable = this module started by hand, nothing to tell."""
    path = os.environ.get("WA_LUNA_TOOLS_READY")
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": time.time(), "pid": os.getpid(),
                                "tools": sorted(mcp._tool_manager._tools)}), encoding="utf-8")


def _arm_ready_stamp():
    """Wrap ``mcp.list_tools`` so the readiness stamp fires on the server's first real response to a
    ``tools/list`` request -- the point the CLI actually has the schemas, verified by reading the
    installed SDK: ``MCPServer`` wires ``on_list_tools=self._handle_list_tools`` into the lowlevel
    ``Server`` at construction (mcp/server/mcpserver/server.py), and ``_handle_list_tools`` awaits
    ``self.list_tools()`` on every such request -- so overriding the instance attribute here is seen
    by every future call without touching the lowlevel wiring itself.

    Tool listing, not the first tool CALL: most turns legitimately call zero tools, and tying the
    stamp to a call would raise ``_live_reply``'s RuntimeError on every one of those. Listing is the
    one signal real proof of a completed handshake and present on every turn regardless."""
    original = mcp.list_tools
    stamped = False

    async def wrapped_list_tools():
        nonlocal stamped
        if not stamped:
            stamped = True
            _stamp_ready()
        return await original()

    mcp.list_tools = wrapped_list_tools


def serve():
    """What ``python -m app.wa.luna.tools_server`` runs -- and what tests/luna_fixture_tools_server.py
    runs too, so an llm test sees the same tool descriptions the live turn does."""
    _prime_board_snapshot()
    apply_board_vocabulary()
    _arm_ready_stamp()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
