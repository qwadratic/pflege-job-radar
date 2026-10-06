#!/usr/bin/env bash
# Cron entrypoint for the nightly LLM-lane run (Ivan, 2026-10-06: the LLM lane was running too often
# -- it moved off pre-deploy entirely, see tools/test_gate.py's own module docstring). This script
# gates on the once-a-day, end-of-workday window and, only inside it, runs `tools/test_gate.py
# nightly` -- which does the real work (whether the lane runs at all depends on that sha's own
# stamp, not on this script).
#
# Install (not done by this repo -- a deliberate human step, same convention as
# tools/agent_note_cron.sh):
#   0 * * * * /home/claude/repo/pflege-board/tools/llm_lane_cron.sh
#
# HOURLY, AROUND THE CLOCK (same reasoning as tools/agent_note_cron.sh's own header): the box's clock
# is UTC, and Europe/Vienna's UTC offset changes with DST, so no single crontab hour field can pin
# 18:00 Vienna in both seasons. Rather than encode that seasonal arithmetic into the crontab line
# (and have it go stale at the next DST transition), this fires every hour and leaves the ENTIRE
# window decision -- weekday AND hour -- to the TZ-aware check below, which needs nothing but the
# box's own accurate UTC clock to always be exactly right. Hourly rather than every 5 minutes like
# agent_note_cron.sh: this gate only ever needs to fire once a day, so a tighter cadence would only
# add more wasted `date` calls.
#
# WHY HOME/PATH ARE SET EXPLICITLY: same reason as tools/agent_note_cron.sh -- cron's own minimal
# environment (no HOME, a bare PATH) makes the `claude` CLI's own session registry
# (~/.claude/daemon/roster.json) hang the whole invocation when HOME is unset, and the LLM lane
# itself spawns `claude` per test.
set -uo pipefail

export HOME=/home/claude
export PATH=/home/claude/.local/bin:/usr/local/bin:/usr/bin:/bin

REPO_DIR="${WA_LLM_LANE_REPO_DIR:-/home/claude/repo/pflege-board}"
# No override point here (Opus review): tools/test_gate.py's own stamp/log/tsv writes are always
# ~/.local/state/pflege-gate, with no env knob of their own -- a separate WA_LLM_LANE_STATE_DIR for
# just this script's lock+log dir gave the false impression that pointing it elsewhere isolated a
# test run, when the python side underneath would still write the real path regardless.
STATE_DIR=/home/claude/.local/state/pflege-gate
LOCK_FILE="$STATE_DIR/nightly.lock"
LOG_DIR="$STATE_DIR/nightly"
VENV_PY="$REPO_DIR/.venv/bin/python"

# --- window: Mon-Fri, hour 18 (end of workday), Europe/Vienna -- regardless of the box's own clock.
# WA_LLM_LANE_TEST_DOW/_HOUR bypass `date` entirely for a test (same convention as
# agent_note_cron.sh's WA_AGENT_NOTE_TEST_HOUR). `date +%u` is 1=Mon .. 7=Sun.
DOW="${WA_LLM_LANE_TEST_DOW:-$(TZ=Europe/Vienna date +%u)}"
HOUR="${WA_LLM_LANE_TEST_HOUR:-$(TZ=Europe/Vienna date +%H)}"
DOW=$((10#$DOW))     # base 10: a leading zero would otherwise read as octal (never happens for %u,
HOUR=$((10#$HOUR))   # kept for symmetry with HOUR, which `date +%H` can print zero-padded)
if [ "$DOW" -gt 5 ] || [ "$HOUR" -ne 18 ]; then
    exit 0
fi

if ! mkdir -p "$STATE_DIR" "$LOG_DIR"; then
    echo "llm_lane_cron: mkdir -p $STATE_DIR $LOG_DIR failed -- no silent skip" >&2
    exit 1
fi

# --- lock: a second run never overlaps (flock -n, non-blocking). A tick that finds the lock held
# skips outright rather than queuing behind it -- the LLM lane can run tens of minutes, and by the
# time a queued tick could start, today's window may already be over. -E 75 pins the lock-conflict
# exit code to 75 (EX_TEMPFAIL) so it can be told apart from every OTHER way flock can fail (bad fd,
# can't open the lock file, ...) -- only rc 75 is a quiet "someone else is already running this",
# everything else is a real problem and exits loud (CLAUDE.md: no safety nets).
exec 9>"$LOCK_FILE"
flock -n -E 75 9
flock_rc=$?
if [ "$flock_rc" -eq 75 ]; then
    exit 0
elif [ "$flock_rc" -ne 0 ]; then
    echo "llm_lane_cron: flock -n on $LOCK_FILE failed unexpectedly (exit $flock_rc)" >&2
    exit "$flock_rc"
fi

# Vars a stray inherited shell can carry that would make a test reach the live rail (see
# tools/test_gate.py's own copy of this list and memory "feedback-tests-never-reach-live-rail").
unset WA_TRANSPORT WA_BRIDGE_URL WA_BRIDGE_TOKEN WA_BRIDGE_INBOUND_TOKEN WA_BRIDGE_PHONE_NUMBER_ID \
      WA_AUTOSEND META_WHATSAPP_ACCESS_TOKEN

LOG_FILE="$LOG_DIR/$(TZ=Europe/Vienna date +%Y-%m-%d).log"
{
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] nightly tick start"
    ( cd "$REPO_DIR" && "$VENV_PY" tools/test_gate.py nightly )
    rc=$?
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] nightly tick end rc=$rc"
} >> "$LOG_FILE" 2>&1

exit "$rc"
