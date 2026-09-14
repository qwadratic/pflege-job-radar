"""A stdio MCP server exposing the pflege-board read API to Luna as real tools, so a turn can look
something up mid-conversation instead of only ever reasoning from the pre-computed
``market_snapshot``/``requirement_scoreboard`` (app/wa/luna_brain.py). Started by the ``claude`` CLI
itself via ``--mcp-config`` (see ``luna_brain.py:Client._live_reply``, which generates the config
pointing at ``python -m app.wa.luna.tools_server`` using the same interpreter the harness itself runs
under, so the ``mcp`` package is guaranteed to be on its path) -- this module is never imported by the
rest of the app, only ever run as that subprocess.

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
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from ... import data as D
from .. import config as C
from .. import slots as SL

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

mcp = MCPServer("pflege_board")


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


@mcp.tool()
def search_postings(city: str = "", department: str = "", role_class: str = "", regierungsbezirk: str = "",
                     housing: bool = False, employment_type: str = "", q: str = "", limit: int = 10) -> list[dict]:
    """Search open Pflege postings on the board. Same filters as GET /api/jobs. Call this whenever
    the candidate names a city, department or region that market_snapshot did not already cover --
    do not guess or say you have no data when a live search would answer it directly."""
    args = {"city": city, "department": department, "role_class": role_class, "regierungsbezirk": regierungsbezirk,
            "housing": housing, "employment_type": employment_type, "q": q, "limit": limit}
    _log_call("search_postings", args)
    filters = {"verify": "live", "sort": "-first_published"}
    if city:
        filters["city"] = city
    if department:
        # TASK-96 review: the model passes the candidate's word (live tool log: department="Intensivstation",
        # 0 rows, "keine passende offene Stelle"); same alias read as luna_brain.market_snapshot.
        filters["department_hint"] = SL.read_department(department) or department
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
    rows = D.filter_jobs(filters)
    return [_job_row(r) for r in rows[:max(1, min(int(limit or 10), 50))]]


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
    question (which hospitals are in a city/region) rather than a posting-level one."""
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
            for c in rows[:max(1, min(int(limit or 10), 50))]]


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


if __name__ == "__main__":
    mcp.run(transport="stdio")
