#!/usr/bin/env bash
# Cron entrypoint for the operator-note worker (TASK-303, Ivan 2026-09-24/25): every 5 minutes, gate
# on the working-hours window and on whether wa_agent_notes plausibly holds anything to do, and only
# then run `python -m app.wa.luna.agent_note_worker` for at most ONE note. See that module's own
# docstring for what a run actually does; this script is only the gate plus the plumbing (env, lock,
# log) cron itself does not give a plain `python -m ...` line for free.
#
# Install (not done by this repo -- deploy/pflege-wa.service's own convention, a deliberate human
# step; see docs/whatsapp.md's runbook section for this feature):
#   */5 * * * * /home/claude/repo/pflege-board/tools/agent_note_cron.sh
# EVERY 5 MINUTES, ALL 24 HOURS (TASK-303, round-1 review, nonblocking finding, Ivan 2026-09-25: fixed
# as a plain bug, not left). An earlier draft of this line was "*/5 9-21 * * *", reasoning that cron's
# hour field only needs to cover ticks that COULD fall in the real, TZ-aware 09:00-22:00 Europe/Vienna
# window checked below. That reasoning missed that the box's own clock is UTC (/etc/localtime =
# Etc/UTC, confirmed), not Vienna: "9-21" fires 09:00-21:55 UTC, which is 11:00-23:55 Vienna in summer
# (CEST, UTC+2) -- so the in-script gate would then only ever see hours 11-21 Vienna in summer (10-21
# in winter, CET, UTC+1), silently losing the first 1-2 hours of the intended 09:00-22:00 window every
# single day, in every season, forever, with nothing anywhere ever flagging it. Covering 09:00-22:00
# Vienna from a UTC crontab in BOTH seasons needs UTC 07:00-21:00 at the widest (Vienna 09:00 is UTC
# 07:00 in summer; Vienna 22:00 is UTC 21:00 in winter) -- rather than encode that seasonal arithmetic
# into a crontab line that would go stale again at the next DST transition, this runs every 5 minutes
# around the clock and leaves the ENTIRE window decision to the TZ-aware check below, which needs
# nothing but the box's own accurate UTC clock to always be exactly right. The added cost is one `date`
# call for every one of the ~204 ticks/day that now start and immediately exit outside the window
# (compared to the "9-21" line) -- negligible, and worth never being wrong about DST again.
#
# WHY HOME/PATH ARE SET EXPLICITLY (AC#3). cron runs with a minimal environment -- no HOME, a bare
# PATH -- and the card's own six live probes (2026-09-25) found the `claude` CLI's own session
# registry (~/.claude/daemon/roster.json) hangs the whole invocation when HOME is unset. Both are set
# here, unconditionally, before anything else runs; this script never sources .env (the python side
# does that itself, app/wa/envfile.py -- systemd-style, not this script's business).
set -uo pipefail

export HOME=/home/claude
export PATH=/home/claude/.local/bin:/usr/local/bin:/usr/bin:/bin

REPO_DIR="${WA_AGENT_NOTE_REPO_DIR:-/home/claude/repo/pflege-board}"
STATE_DIR="${WA_AGENT_NOTE_STATE_DIR:-/home/claude/.local/state/pflege-wa-agent-notes}"
SQLITE_PATH="${WA_SQLITE_PATH:-$REPO_DIR/data/wa.sqlite}"
CLAUDE_BIN="${WA_AGENT_NOTE_CLAUDE_BIN:-/home/claude/.local/bin/claude}"
WINDOW_START="${WA_AGENT_NOTE_WINDOW_START_HOUR:-9}"
WINDOW_END="${WA_AGENT_NOTE_WINDOW_END_HOUR:-22}"
VENV_PY="$REPO_DIR/.venv/bin/python"
# The exact command that runs the worker -- deliberately word-split on whitespace (the one place in
# this script an expansion is left unquoted on purpose), so the real default (4 words: the venv
# python, -m, the module name) and a test's override (one word: a fake script path) both work with no
# extra quoting logic. None of these words ever contains a space of its own.
WORKER_CMD="${WA_AGENT_NOTE_WORKER_CMD:-$REPO_DIR/.venv/bin/python -m app.wa.luna.agent_note_worker}"

WORKER_LOG="$STATE_DIR/worker.log"
LOCK_FILE="$STATE_DIR/agent_note_cron.lock"
HEALTH_FILE="$STATE_DIR/health.json"

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log() {
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    echo "[$(now_iso)] $*" >> "$WORKER_LOG"
}

# write_health ok(true|false) problem note_id card_id -- health.json in the exact shape
# app.wa.luna.agent_note_worker.write_health writes, for the two guards below that fail early enough
# that python never even started (so nothing else would ever write this tick's outcome). Built with
# the real repo venv's python (never $WORKER_CMD, which a test may have pointed at a fake binary) so
# the JSON comes from json.dumps, not hand-escaped shell string-building.
write_health() {
    local ok="$1" problem="$2" note_id="$3" card_id="$4"
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    "$VENV_PY" - "$ok" "$problem" "$note_id" "$card_id" > "$HEALTH_FILE.tmp" <<'PYEOF' && mv "$HEALTH_FILE.tmp" "$HEALTH_FILE"
import json, sys
from datetime import datetime, timezone
ok, problem, note_id, card_id = sys.argv[1:5]
print(json.dumps({"ok": ok == "true", "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                   "problem": problem or None, "note_id": int(note_id) if note_id else None,
                   "card_id": card_id or None}))
PYEOF
}

# --- window: TZ=Europe/Vienna regardless of the box's own clock (the box runs UTC) -- cron flavours
# disagree on CRON_TZ, so this is checked here rather than trusted to the crontab line's own timezone
# handling. WA_AGENT_NOTE_TEST_HOUR bypasses `date` entirely for a test.
HOUR="${WA_AGENT_NOTE_TEST_HOUR:-$(TZ=Europe/Vienna date +%H)}"
HOUR=$((10#$HOUR))   # base 10: `date +%H` can print a leading zero, which bash would otherwise read as octal
if [ "$HOUR" -lt "$WINDOW_START" ] || [ "$HOUR" -ge "$WINDOW_END" ]; then
    exit 0
fi

mkdir -p "$STATE_DIR" 2>/dev/null || true

# --- lock: ticks never overlap (AC/plan: flock -n). Non-blocking -- a tick that finds the lock held
# means the previous one is still running (most likely a slow claude -p call); this tick skips rather
# than queue behind it, and the next one 5 minutes later tries again.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    exit 0
fi

# --- binary guard (AC#2): the /home/claude/.local/bin/claude SYMLINK itself, not a bare PATH lookup,
# must resolve to a regular, executable, non-empty file -- catches exactly the failure the card names:
# a half-finished CLI update once left a 0-byte binary behind.
RESOLVED="$(readlink -f "$CLAUDE_BIN" 2>/dev/null || true)"
if [ -z "$RESOLVED" ] || [ ! -f "$RESOLVED" ] || [ ! -x "$RESOLVED" ] || [ ! -s "$RESOLVED" ]; then
    problem="claude binary guard failed: $CLAUDE_BIN does not resolve to a non-empty executable file (resolved: '${RESOLVED:-<none>}')"
    log "$problem"
    write_health false "$problem" "" ""
    exit 1
fi

# --- cheap sqlite prefilter: a hint, not the authority -- open_agent_notes() (python) is the real
# query, and this only exists so a tick with definitely nothing to do never starts python, let alone
# claude. A missing db or table is a PROBLEM, never silently read as "0 rows, nothing to do".
if [ ! -f "$SQLITE_PATH" ]; then
    problem="sqlite prefilter: db not found at $SQLITE_PATH"
    log "$problem"
    write_health false "$problem" "" ""
    exit 1
fi
PREFILTER_ERR="$STATE_DIR/.agent_note_prefilter_err"
COUNT="$(sqlite3 -readonly "$SQLITE_PATH" \
    "select count(*) from wa_agent_notes where status in ('pending','in_progress') or (status in ('done','blocked') and notified_at is null);" \
    2>"$PREFILTER_ERR")"
RC=$?
if [ "$RC" -ne 0 ] || ! [[ "$COUNT" =~ ^[0-9]+$ ]]; then
    problem="sqlite prefilter failed (rc=$RC): $(tr '\n' ' ' < "$PREFILTER_ERR" 2>/dev/null)"
    rm -f "$PREFILTER_ERR"
    log "$problem"
    write_health false "$problem" "" ""
    exit 1
fi
rm -f "$PREFILTER_ERR"
if [ "$COUNT" -eq 0 ]; then
    exit 0
fi

# NO LOG TRUNCATION (TASK-303 item E, Ivan 2026-09-25; CLAUDE.md "no safety nets" -- a size cap nobody
# asked for): the wrapper used to trim worker.log to roughly LOG_MAX_BYTES/5 every time it grew past
# LOG_MAX_BYTES, silently discarding everything before the trim point. This tick only ever appends two
# short lines plus whatever the worker itself prints, and (see above) a tick outside the window or with
# an empty queue writes NOTHING to this file at all -- growth is bounded by real activity, not by an
# invented ceiling. If the log ever does need bounding, that is Ivan's call to make explicitly, per
# CLAUDE.md, not a default this script invents for him.

# --- run: the repo venv (or a test's fake WORKER_CMD), from the repo dir, output appended with a
# timestamp on either side so a hung tick is visible as a start line with no matching end line.
#
# CRASH VISIBILITY (TASK-303 item C, round-1 review): captured BEFORE the run, health.json's PRIOR
# content (if any) is removed so "does health.json exist after the worker ran" is an unambiguous yes/no
# -- immune to the same-second write collisions a pure mtime/hash comparison could not rule out (ticks
# are 5 minutes apart in production, but this script's own tests invoke it many times inside one
# wall-clock second). If the worker crashes before writing its own (an import-time envfile/config
# error, a signal, anything not already caught by agent_note_worker.py's own main() top-level handler)
# health.json is missing afterward; the wrapper then either writes a generic ok:false naming the exit
# code (rc != 0: a real problem worth surfacing) or, on a clean rc=0 with nothing new written (the
# worker quietly lost a claim race -- a legitimate, quiet outcome, not a crash), restores the PRIOR
# tick's own health rather than leave none where there used to be some.
PRIOR_HEALTH=""
if [ -f "$HEALTH_FILE" ]; then
    PRIOR_HEALTH="$(cat "$HEALTH_FILE" 2>/dev/null || true)"
    rm -f "$HEALTH_FILE"
fi

{
    echo "[$(now_iso)] tick start (queue count=$COUNT)"
    ( cd "$REPO_DIR" && $WORKER_CMD )
    rc=$?
    echo "[$(now_iso)] tick end rc=$rc"
} >> "$WORKER_LOG" 2>&1

if [ ! -f "$HEALTH_FILE" ]; then
    if [ "$rc" -ne 0 ]; then
        problem="worker exited rc=$rc without a health write this tick -- see worker.log"
        log "$problem"
        write_health false "$problem" "" ""
    elif [ -n "$PRIOR_HEALTH" ]; then
        printf '%s' "$PRIOR_HEALTH" > "$HEALTH_FILE"
    fi
fi

# The wrapper's own exit code now mirrors the worker's (agent_note_worker.py's own docstring: "cron.sh
# and a human reading cron's own mail both get a plain, uniform signal from this" -- true only if the
# wrapper actually propagates it, which it did not before this fix: the `{ ...; } >> log 2>&1` group's
# own exit status was always its LAST command's, the closing echo, always 0, regardless of $rc).
exit "$rc"
