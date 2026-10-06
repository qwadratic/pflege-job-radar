#!/usr/bin/env bash
# Cron entrypoint for tools/sip_guard_watch.py (SIP gateway guard, Ivan 2026-10-01).
# Same shape as tools/disk_janitor_cron.sh: explicit HOME/PATH, a non-blocking flock so
# overlapping ticks skip, and one log line per run.
#
# Install (crontab of user claude):
#   */5 * * * * /home/claude/repo/pflege-board/tools/sip_guard_cron.sh
#
# fail2ban itself (jail "asterisk", /etc/fail2ban/jail.d/zz-sip-guard.local) does the
# banning; this job only reads its events and asterisk's log and mails Ivan.
set -uo pipefail

export HOME=/home/claude
export PATH=/home/claude/.local/bin:/usr/local/bin:/usr/bin:/bin

REPO_DIR="${SIP_GUARD_REPO_DIR:-/home/claude/repo/pflege-board}"
STATE_DIR="${SIP_GUARD_STATE_DIR:-/home/claude/.local/state/sip-guard}"
LOG_FILE="$STATE_DIR/cron.log"
LOCK_FILE="$STATE_DIR/sip_guard_cron.lock"

mkdir -p "$STATE_DIR" 2>/dev/null || true

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    exit 0
fi

( cd "$REPO_DIR" && "$REPO_DIR/.venv/bin/python" tools/sip_guard_watch.py ) >> "$LOG_FILE" 2>&1
