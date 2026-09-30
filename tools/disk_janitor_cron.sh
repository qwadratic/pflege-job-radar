#!/usr/bin/env bash
# Cron entrypoint for tools/disk_janitor.py (TASK-315 AC#10). Same shape as
# tools/agent_note_cron.sh: explicit HOME/PATH (cron's own environment is
# minimal), a non-blocking flock so overlapping ticks skip rather than queue
# behind a slow rsync, and a log line per run. Unlike agent_note_cron.sh,
# this wrapper does not need to synthesize health.json on an early guard
# failure -- tools/disk_janitor.py always writes its own health.json (even
# on a below-threshold no-op tick), so this script's only job is the lock,
# the environment, and running the real thing with --apply.
#
# Install (not done by this repo -- a deliberate human step, same convention
# as agent_note_cron.sh; see docs/whatsapp.md's runbook section):
#   */15 * * * * /home/claude/repo/pflege-board/tools/disk_janitor_cron.sh
#
# Every 15 minutes, all 24 hours -- there is no time-of-day window here
# (disk space does not respect Vienna business hours). tools/disk_janitor.py
# itself is the only gate that matters: below --min-free-mb it acts, at or
# above it, it logs sizes and does nothing. A 15-minute cadence means a
# sudden fill is caught within 15 minutes without running rsync/ssh on every
# single tick when the disk is nowhere near the threshold (the overwhelming
# common case).
set -uo pipefail

export HOME=/home/claude
export PATH=/home/claude/.local/bin:/usr/local/bin:/usr/bin:/bin

REPO_DIR="${WA_DISK_JANITOR_REPO_DIR:-/home/claude/repo/pflege-board}"
STATE_DIR="${WA_DISK_JANITOR_STATE_DIR:-/home/claude/.local/state/disk-janitor}"
VENV_PY="$REPO_DIR/.venv/bin/python"
MIN_FREE_MB="${WA_DISK_JANITOR_MIN_FREE_MB:-3000}"
# The exact command that runs the janitor -- deliberately word-split on
# whitespace (same convention as agent_note_cron.sh's WORKER_CMD), so a
# test's override (one word: a fake script path) and the real default (the
# venv python, the script path, --apply, --min-free-mb N: five words) both
# work with no extra quoting logic. None of these words ever contains a
# space of its own.
JANITOR_CMD="${WA_DISK_JANITOR_CMD:-$VENV_PY $REPO_DIR/tools/disk_janitor.py --apply --min-free-mb $MIN_FREE_MB}"

LOG_FILE="$STATE_DIR/cron.log"
LOCK_FILE="$STATE_DIR/disk_janitor_cron.lock"

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

mkdir -p "$STATE_DIR" 2>/dev/null || true

# --- lock: ticks never overlap (a slow rsync must never stack a second
# janitor run on top of itself). Non-blocking -- a tick that finds the lock
# held means the previous one is still running; this tick skips rather than
# queue behind it, and the next one tries again on its own schedule.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    exit 0
fi

{
    echo "[$(now_iso)] tick start"
    ( cd "$REPO_DIR" && $JANITOR_CMD )
    rc=$?
    echo "[$(now_iso)] tick end rc=$rc"
} >> "$LOG_FILE" 2>&1

exit "$rc"
