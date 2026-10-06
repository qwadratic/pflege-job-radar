#!/usr/bin/env python3
"""UserPromptSubmit hook: put the operator inbox's current state in front of the working session
(Ivan, 2026-09-24; rewritten for TASK-303, 2026-09-25).

WHAT CHANGED FROM TASK-297. That card had this hook poll a single backlog task's own unchecked
acceptance criteria -- a stand-in for a queue that, at the time, nothing else was draining. TASK-303
built the real consumer (app/wa/luna/agent_note_worker.py, started by cron): it claims a note, decodes
it, writes a backlog card and hands off to this exact session by name, all without this hook's help.
What THIS hook now shows is a compact status line over the REAL queue (wa_agent_notes) and the
worker's own health file, so a note the hand-off message somehow missed -- or a worker that is stuck --
is still visible on this session's very next turn, without polling anything in between turns.

WHAT IT PRINTS. Nothing, unless wa_agent_notes holds an open note (pending, in_progress, handed_off, or
finished but not yet notified) or a recently-blocked one, or the worker's own health.json says ok=false
recently. Stdout from a UserPromptSubmit hook is injected as context, so silence really is free; a
"queue is empty" line on every single turn would be the very cost this design exists to avoid.

IT MUST NEVER BREAK A TURN. Every failure -- the database missing or unreadable, a table not there yet,
a malformed row, a missing or corrupt health.json, the read timing out -- is caught and treated as
"nothing to show", never raised. A hook that made a turn fail because a queue it was only reporting on
was unreadable would be worse than no hook at all, so there is no failure mode here that is allowed to
be loud. That is deliberately the opposite of this repo's usual "fail loudly" rule (CLAUDE.md): the rule
is about work that must not silently not-happen, and this file does no work -- it only reports. A silent
hook loses one turn's reminder; a raising one loses the turn.

Reads the database through a read-only SQLite URI with a short busy timeout (never the app's own
app.wa.store, which creates tables and takes a write-capable connection -- this hook must never be
what turns an absent database into an accidentally-created empty one, and must never be what a webhook
process is left waiting behind). Every path is overridable by env, matching
tools/agent_note_cron.sh's own WA_SQLITE_PATH/WA_AGENT_NOTE_STATE_DIR.

Wire-up lives in .claude/settings.json (project scope, versioned with the repo, already pointed at
this file) -- this module does not touch it.
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("WA_SQLITE_PATH", "").strip() or "/home/claude/repo/pflege-board/data/wa.sqlite"
STATE_DIR = (os.environ.get("WA_AGENT_NOTE_STATE_DIR", "").strip()
            or "/home/claude/.local/state/pflege-wa-agent-notes")
HEALTH_PATH = os.path.join(STATE_DIR, "health.json")

DB_TIMEOUT_SEC = float(os.environ.get("WA_OPERATOR_QUEUE_DB_TIMEOUT_SEC", "3") or "3")
# A blocked note past this age is presumed already seen/handled; only recently-blocked ones are worth
# a repeat reminder on every turn (a finished-but-UNDELIVERED note, below, has no such window -- its
# completion message genuinely never went out, which stays worth surfacing at any age).
BLOCKED_RECENT_HOURS = float(os.environ.get("WA_OPERATOR_QUEUE_BLOCKED_RECENT_HOURS", "24") or "24")
# A health.json problem older than this is presumed stale/superseded -- the worker runs every 5
# minutes inside its window and a genuinely still-open problem gets rewritten with a fresh timestamp
# on the very next tick that finds work, so 30 minutes is generous slack, not the expected gap.
HEALTH_RECENT_MIN = float(os.environ.get("WA_AGENT_NOTE_HEALTH_RECENT_MIN", "30") or "30")

BODY_PREVIEW_CHARS = 120

_OPEN_SQL = """
select id, status, kind, body, created_at, task_id from wa_agent_notes
where status in ('pending','in_progress','handed_off')
   or (status in ('done','blocked') and notified_at is null)
   or (status='blocked' and finished_at is not null and finished_at>=?)
order by created_at, id
"""


def _age(iso, now):
    """A short, single-unit age like '3m'/'2h'/'5d' -- compact on purpose, this is one line per note
    among possibly several. '?' for anything that does not parse rather than raising: a malformed
    timestamp must not be why this hook breaks a turn.

    ROUND-1 REVIEW, NONBLOCKING, FIXED AS A PLAIN BUG (Ivan, 2026-09-25): ``now - dt`` used to sit
    OUTSIDE this try, so a timezone-NAIVE ``iso`` (``now`` is always aware, see open_notes/
    health_problem) raised TypeError one level up, inside open_notes()'s own row loop -- also not
    wrapped per-row. main() catches everything, so this never broke a turn, but it DID blank the
    WHOLE hook (every other, well-formed note included) over one bad row. Moving the subtraction
    inside the same try -- already catching TypeError -- fixes that without changing what a caller
    sees for a single bad row (still '?')."""
    try:
        dt = datetime.fromisoformat(iso)
        secs = (now - dt).total_seconds()
    except (TypeError, ValueError):
        return "?"
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"{int(secs)}s"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"


def open_notes(db_path=None, now=None):
    """-> a list of open-note dicts (id, status, kind, age, card, preview), oldest first, or [] for
    ANY reason at all -- missing file, missing table, a locked/corrupt database, a query timeout.
    Never raises."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=BLOCKED_RECENT_HOURS)).replace(microsecond=0).isoformat()
    path = db_path or DB_PATH
    try:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=DB_TIMEOUT_SEC)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(_OPEN_SQL, (cutoff,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    out = []
    for r in rows:
        body = (r["body"] or "").replace("\n", " ").strip()
        if len(body) > BODY_PREVIEW_CHARS:
            body = body[:BODY_PREVIEW_CHARS - 1].rstrip() + "…"
        out.append({"id": r["id"], "status": r["status"], "kind": r["kind"],
                   "age": _age(r["created_at"], now), "card": r["task_id"] or "-", "body": body})
    return out


def health_problem(health_path=None, now=None):
    """-> (problem text, age string) when health.json says ok=false and is recent, else None. Any
    failure to read/parse it (missing file, corrupt JSON, an unexpected shape) is "nothing to show",
    same contract as open_notes()."""
    now = now or datetime.now(timezone.utc)
    try:
        with open(health_path or HEALTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("ok") is not False:
        return None
    problem = data.get("problem")
    at = data.get("at")
    if not problem or not at:
        return None
    try:
        age_secs = (now - datetime.fromisoformat(at)).total_seconds()
    except (TypeError, ValueError):
        return None
    if age_secs < 0 or age_secs > HEALTH_RECENT_MIN * 60:
        return None
    return str(problem), _age(at, now)


def render(notes, problem):
    if not notes and not problem:
        return ""
    lines = []
    if notes:
        lines.append(f"Operator inbox (WhatsApp test rail) -- {len(notes)} open note(s):")
        for n in notes:
            lines.append(f"  #{n['id']} {n['status']} {n['kind']} age={n['age']} card={n['card']} "
                        f"\"{n['body']}\"")
        lines.append("pending/in_progress: not handed off yet (the cron worker is still on it). "
                    "handed_off: yours -- backlog task view <card> --plain, then close it with "
                    "app/wa/luna/agent_notes.py --done/--blocked.")
    if problem:
        text, age = problem
        lines.append(f"Operator-note worker health problem ({age} ago): {text}")
    return "\n".join(lines) + "\n"


def main():
    try:
        notes = open_notes()
        problem = health_problem()
        out = render(notes, problem)
    except Exception:
        return 0
    if out:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
