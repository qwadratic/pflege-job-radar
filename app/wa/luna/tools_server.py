"""A stdio MCP server exposing the board read API to Luna as real tools, so a turn can look
something up mid-conversation instead of only ever reasoning from the pre-computed
``market_snapshot``/``requirement_scoreboard`` (app/wa/luna_brain.py). Started by the ``claude`` CLI
itself via ``--mcp-config`` (see ``luna_brain.py:Client._live_reply``, which generates the config
pointing at ``python -m app.wa.luna.tools_server`` using the same interpreter the harness itself runs
under, so the ``mcp`` package is guaranteed to be on its path) -- this module is never imported by the
rest of the app, only ever run as that subprocess.

Three kinds of tool (TASK-110, Ivan 2026-09-16, after the housing filter existed for a task and was
never once used by the model):

1. ``search_postings``/``get_posting``/``list_clinics`` -- the general board queries.
2. Purpose-built tools with the filter already preset -- ``search_postings_with_housing``,
   ``list_clinics_with_housing``, ``list_cities_with_postings``, ``count_postings``. A filter the
   model has to assemble itself out of bare parameter names goes unused; a tool whose name *is* the
   candidate's need does not.
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
included (TASK-110 review). After the tools are registered this server stamps
``WA_LUNA_TOOLS_READY``, which is how ``luna_brain._live_reply`` sees that the turn actually had the
board tools -- a server that dies or is dropped leaves the CLI exiting 0 with a normal-looking reply.

Every call is appended to a JSONL log (``config.LUNA_SESSION_DIR/tool_calls.jsonl``) so a test can
prove a tool was actually invoked -- not just that the reply happened to look right afterward.

Reuses the exact same query functions the rest of the harness already relies on: ``D.filter_jobs``/
``D.filter_clinics`` (the same ones GET /api/jobs and /api/clinics use, and the same ones
``app/wa/brain.py:jobs_for`` calls), so a tool call and the harness's own pre-computed snapshot can
never drift against each other.

Must be launched as ``python -m app.wa.luna.tools_server`` with the repo root as (or on the path
of) the working directory -- a relative import needs real package context, so running this file
directly (``python tools_server.py``) cannot work no matter what sys.path says. See
``luna_brain.py``'s mcp-config generator for the exact command/cwd this is started with.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ... import data as D
from ... import search as SE
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
    from . import contacts as CT   # TASK-64, built in parallel -- absent until that task lands
except ImportError:
    CT = None

_REPO_ROOT = Path(__file__).resolve().parents[3]

mcp = MCPServer("jobs")   # same name as luna_brain.MCP_SERVER_NAME; model-visible, no brand

# LIVE_BASE (board_vocabulary.py): open postings the verifier re-fetched, newest first -- the base of
# every posting query here and of app/wa/slots.py:filters()/market_snapshot, so a tool result and the
# harness's own shortlist count the same rows.
RESULT_LIMIT = 50


def _session_dir():
    """This server runs as a subprocess the CLI spawns fresh -- it does its own import of
    app.wa.config, so a test's ``monkeypatch.setattr(config, "LUNA_SESSION_DIR", ...)`` in the
    *parent* process never reaches it. ``luna_brain.py:_mcp_config_path`` passes the parent's
    current value through explicitly as the WA_LUNA_SESSION_DIR env var for exactly this reason;
    fall back to the config default only if that is somehow unset (e.g. this file run by hand)."""
    override = os.environ.get("WA_LUNA_SESSION_DIR")
    return Path(override) if override else C.LUNA_SESSION_DIR


def _log_call(name, args):
    """Append-only, best-effort: a logging failure must never break a tool call itself."""
    try:
        d = _session_dir()
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"tool": name, "args": args, "at": time.time()}, ensure_ascii=False)
        with open(d / "tool_calls.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _job_row(r):
    return {"posting_id": r.get("posting_id"), "title": r.get("title"),
            "clinic_id": r.get("clinic_id"), "clinic_name": r.get("clinic_name") or r.get("employer"),
            "city": r.get("city") or r.get("clinic_town"), "department": r.get("department_hint"),
            "regierungsbezirk": r.get("regierungsbezirk"), "housing": bool(r.get("enr_housing")),
            "employment_types": r.get("employment_types"),
            "source_url": r.get("source_url") or r.get("external_url")}


_city = city_of                 # one clinic/city identity for every count here and in the vocabulary
_clinic_name = clinic_name_of


def _limit(value, default=10):
    return max(1, min(int(value or default), RESULT_LIMIT))


def _job_filters(city="", department="", role_class="", regierungsbezirk="", housing=False, employment_type="", q=""):
    """The GET /api/jobs query for these arguments, with the department word read the way the rest of
    the harness reads it. Shared by every posting tool below so the general search and the preset ones
    can never disagree about what a candidate's word means."""
    filters = dict(LIVE_BASE)
    if city:
        filters["city"] = city
    if department:
        # TASK-96 review: the model passes the candidate's word (live tool log: department="Intensivstation",
        # 0 rows, "keine passende offene Stelle"). TASK-104: the same reading as luna_brain.market_snapshot; a
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
    return filters


# --- vocabulary, read off the live board ----------------------------------------------------
# AC1 of TASK-110: the model only ever learned the filters from bare parameter names, so it never used
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
    cannot see being missed (TASK-110 review). A missing or unreadable file is a bug in the parent and
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
    "search_postings": ("board", "department", "regierungsbezirk", "role_class", "employment_type", "housing"),
    "search_postings_with_housing": ("housing", "department", "regierungsbezirk"),
    "list_clinics_with_housing": ("housing", "regierungsbezirk"),
    "list_cities_with_postings": ("department", "housing", "regierungsbezirk"),
    "count_postings": ("department", "regierungsbezirk", "role_class", "employment_type", "housing"),
    "list_clinics": ("regierungsbezirk",),
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
                     housing: bool = False, employment_type: str = "", q: str = "", limit: int = 10) -> list[dict]:
    """Search open Pflege postings on the board. Same filters as GET /api/jobs. Call this whenever
    the candidate names a city, department or region that market_snapshot did not already cover --
    do not guess or say you have no data when a live search would answer it directly. Every filter the
    candidate stated belongs in the call; for a flat, search_postings_with_housing is the shorter way."""
    args = {"city": city, "department": department, "role_class": role_class, "regierungsbezirk": regierungsbezirk,
            "housing": housing, "employment_type": employment_type, "q": q, "limit": limit}
    _log_call("search_postings", args)
    rows = D.filter_jobs(_job_filters(city, department, role_class, regierungsbezirk, housing, employment_type, q))
    return [_job_row(r) for r in rows[:_limit(limit)]]


@mcp.tool()
def search_postings_with_housing(city: str = "", department: str = "", regierungsbezirk: str = "",
                                  limit: int = 10) -> list[dict]:
    """Open postings the board marks as coming with a flat -- the housing filter is already on. Call this
    the moment the candidate needs an Unterkunft/Wohnung and a place or department is on the table: a
    stated need must be in the call, not only in your reply. Empty result = there is nothing with a flat
    under these criteria (say so plainly; list_cities_with_postings(housing=true) gives the real
    alternatives), never a reason to offer a posting without the mark."""
    args = {"city": city, "department": department, "regierungsbezirk": regierungsbezirk, "limit": limit}
    _log_call("search_postings_with_housing", args)
    rows = D.filter_jobs(_job_filters(city=city, department=department, regierungsbezirk=regierungsbezirk,
                                      housing=True))
    return [_job_row(r) for r in rows[:_limit(limit)]]


@mcp.tool()
def list_clinics_with_housing(city: str = "", regierungsbezirk: str = "", limit: int = 10) -> list[dict]:
    """The clinics that have at least one open posting the board marks with a flat, each with how many.
    Call this for a clinic-level housing question ("welche Kliniken bieten eine Wohnung"), where
    search_postings_with_housing answers the posting-level one."""
    _log_call("list_clinics_with_housing", {"city": city, "regierungsbezirk": regierungsbezirk, "limit": limit})
    rows = D.filter_jobs(_job_filters(city=city, regierungsbezirk=regierungsbezirk, housing=True))
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
                                           "postings_with_housing": 0})
        entry["postings_with_housing"] += 1
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
    rows = D.filter_jobs(_job_filters(department=department, regierungsbezirk=regierungsbezirk, housing=housing))
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
    """How many open postings, distinct clinics and cities match these criteria, plus how many of them come
    with a flat. Call this for a number ("wie viele Stellen haben Sie in X") instead of counting rows from a
    search yourself, and always with at least one filter: the board-wide total is already in
    market_snapshot.open_jobs, so calling this with every parameter empty only re-derives a number you have.
    with_housing next to postings is the honest picture for a candidate who needs a flat: open positions
    there, and how many of them actually come with one."""
    args = {"city": city, "department": department, "role_class": role_class, "regierungsbezirk": regierungsbezirk,
            "housing": housing, "employment_type": employment_type}
    if not any(args.values()):
        # TASK-110 review: live runs kept calling this with every parameter empty to re-derive a number the
        # payload already carries -- two seconds of the turn for nothing, and the prompt rule saying so did
        # not hold across runs (3 runs 2026-09-16, two of them did it). The refusal says where the number is,
        # so the turn just answers; the log keeps the attempt, marked, so a test can see it was not answered.
        _log_call("count_postings", {**args, "refused": "no filter"})
        raise ToolError("count_postings needs at least one filter (city, department, role_class, "
                        "regierungsbezirk, housing, employment_type). The board-wide total is already in "
                        "this turn's market_snapshot.open_jobs -- answer that from the payload, no call.")
    _log_call("count_postings", args)
    filters = _job_filters(city, department, role_class, regierungsbezirk, housing, employment_type)
    rows = D.filter_jobs(filters)
    return {"postings": len(rows), "clinics": len({clinic_key(r) for r in rows if clinic_key(r)}),
            "cities": len({_city(r) for r in rows if _city(r)}),
            "with_housing": sum(1 for r in rows if D.offers_housing(r)),
            "filters": {k: v for k, v in filters.items() if k not in LIVE_BASE}}


@mcp.tool()
def get_posting(posting_id: int) -> dict | None:
    """Full detail (still board-public fields only) for one posting id already seen in a search
    result or market_snapshot, or null if it no longer exists in the live snapshot."""
    _log_call("get_posting", {"posting_id": posting_id})
    row = next((j for j in D.jobs() if j.get("posting_id") == posting_id), None)
    return _job_row(row) if row else None


@mcp.tool()
def list_clinics(city: str = "", regierungsbezirk: str = "", has_jobs: bool = True, limit: int = 10) -> list[dict]:
    """List clinics on the board, same filters as GET /api/clinics. Use this for a clinic-level
    question (which hospitals are in a city/region) rather than a posting-level one; for clinics with a
    flat, list_clinics_with_housing."""
    _log_call("list_clinics", {"city": city, "regierungsbezirk": regierungsbezirk, "has_jobs": has_jobs, "limit": limit})
    filters = {}
    if city:
        filters["city"] = city
    if regierungsbezirk:
        filters["regierungsbezirk"] = regierungsbezirk
    if has_jobs:
        filters["has_jobs"] = "1"
    rows = D.filter_clinics(filters)
    return [{"clinic_id": c.get("clinic_id"), "name": c.get("name"), "town": c.get("town"),
             "regierungsbezirk": c.get("regierungsbezirk"), "beds": c.get("beds")}
            for c in rows[:_limit(limit)]]


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
# put it in a WhatsApp bubble -- asked who we are, this model once named the repo (TASK-100), and
# prompts.py:IDENTITY forbids ever naming another company, brand, website or product. So the served
# text loses its YAML front matter (skill-loader metadata naming the host and the product), the whole
# "Where things are" section (host table, project, key, key-scope audit) and every URL; the
# allowlisted board API below is the whole data door she needs (TASK-110 review).
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


def _verified(p):
    """Every other tool searches the re-verified rows (LIVE_BASE). Without this the two doors answer the
    same question differently -- live 2026-09-16: count_postings(city='Coburg') 2 vs GET /api/jobs?city=Coburg
    39, München 369 vs 499 -- and the open board carries postings the verifier never confirmed or found
    error/blocked/gone. An explicit verify= in the query still wins: asking for the whole open board on
    purpose stays possible, it just cannot happen by accident (TASK-110 review)."""
    return p if p.get("verify") else {**p, "verify": LIVE_BASE["verify"]}


def _api_jobs(p):
    p = _bounded(_verified(p))
    out = D.page(D.filter_jobs(p), p, BOARD_API_MAX_ROWS)
    out["rows"] = D.redact(out["rows"], None)
    return out


def _api_clinics(p):
    p = _bounded(p)
    out = D.page(D.filter_clinics(p), p, BOARD_API_MAX_ROWS)
    out["rows"] = D.redact(out["rows"], None)
    return out


def _api_clinic(clinic_id, p):
    c = D.clinic(clinic_id)
    if not c:
        raise ToolError(f"unknown clinic {clinic_id!r}")
    jobs = D.filter_jobs(_verified({"clinic_id": clinic_id, "sort": "-first_published"}))
    # `runs` is owner-only on the app API itself (app/main.py:api_clinic) -- an anonymous caller gets the
    # empty list there and gets it here. `jobs` is the route's own unpaged list, bounded like every other
    # result here, with the full number next to it so a cut list can never read as the whole one.
    return {**c, "jobs_total": len(jobs), "jobs": D.redact(jobs[:BOARD_API_MAX_ROWS], None), "runs": []}


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


@mcp.tool()
def board_api_get(path: str, query: str = "") -> dict | list:
    """One read-only GET against the public board API, for a question the tools above do not cover (a
    filter they do not preset, a facet list, a fuzzy name search). path is one of: /api/jobs,
    /api/clinics, /api/clinics/{clinic_id}, /api/cities, /api/facets, /api/taxonomy, /api/search. query is
    the query string ('city=Coburg&housing=1&limit=5'); read_board_docs('api') documents every filter and
    the {total, limit, offset, next_offset, rows} envelope -- 'total' is the full number of matches however
    few rows come back. Same rows as the tools above (verify=live, the re-verified postings) unless you
    pass verify= yourself; at most 25 rows per call, a bigger limit is an error, so ignore what the docs
    say about limit=999999. Read-only public board data: any other path is an error, personal data
    (contact e-mail addresses) is removed, and nothing here can change anything."""
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
        return handler(*args, params)
    except HTTPException as exc:      # the app API's own 400s (a non-integer limit, a negative offset)
        raise ToolError(f"GET {path} rejected the query {query!r}: {exc.detail}")


# Captured before any vocabulary is appended, so apply_board_vocabulary() is idempotent (the fixture
# tools server in tests/ imports this module and applies it again with its own board).
_BASE_DESCRIPTIONS = {name: tool.description for name, tool in mcp._tool_manager._tools.items()}


def _stamp_ready():
    """Tell the parent this turn actually got the board tools.

    A tools server that dies at start, or that the CLI drops for missing its connect deadline, is
    invisible from outside: ``claude -p`` still exits 0 with ``is_error`` false and a normal-looking
    reply, and the result envelope carries no MCP server status at all (probed, CLI 2.1.270) -- so the
    turn runs with a system prompt that says "TOOLS (mandatory, not optional)", names nine tools that are
    not there, and answers about the board from nothing. luna_brain._live_reply raises when this file is
    missing after the run. No variable = this module started by hand, nothing to tell."""
    path = os.environ.get("WA_LUNA_TOOLS_READY")
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": time.time(), "pid": os.getpid(),
                                "tools": sorted(mcp._tool_manager._tools)}), encoding="utf-8")


def serve():
    """What ``python -m app.wa.luna.tools_server`` runs -- and what tests/luna_fixture_tools_server.py
    runs too, so an llm test sees the same tool descriptions the live turn does."""
    apply_board_vocabulary()
    _stamp_ready()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
