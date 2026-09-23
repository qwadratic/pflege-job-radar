---
id: TASK-276
title: >-
  The debug recording starts before the phone lock is taken, so it expires on
  exactly the slow ops it was built for -- and a failed pull is silent
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 15:05'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 223000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/dispatcher.py:107. Severity: degraded. 

HOW IT HAPPENS: The identity watcher holds the lock for 25 s while a long send is queued. The recording starts at claim time, spends 25 s filming the other lane's screen, then the 150 s send runs past the 180 s limit; the recording ends before the tick wait and park. Or the pull fails and stop_recording returns None -- nothing is journalled, and the absence is discovered weeks later by whoever needed it.

WHAT IT COSTS: After an incident you look for op.<id>.mp4 and find it truncated to someone else's screen, or absent with nothing explaining why, while health reports debug_capture: true and implies coverage that does not exist for the slowest, most interesting operations.

PROPOSED DIRECTION (not a decision): Start the recording once the operation actually holds the phone rather than when the row is claimed, so the 180 s budget covers the work and we stop filming the other lane while we wait. Size the limit against the real worst case (90-150 s send plus its waits) or accept a truncated tail deliberately and say so. Journal a stop that returns None -- a missing artefact should be visible the moment it goes missing.

VERIFICATION NOTES: run_one calls start_recording at dispatcher.py:107, before _resolve/method at 109-110; every dispatched method then calls take_phone, which waits up to D.LOCK_TIMEOUT_SEC = 30 s (driver.py:38) for the flock. screenrecord runs with --time-limit 180 (adb_driver.py:1671), so the budget starts during the wait. Contention is real even with the FIFO queue: IdentityWatcher takes the same lock and waits a send-sized timeout (watcher.py:306-310, executor.auto_match_media), and the colleague's daemon cycles on it every 15 s. A send owns the lock 90-150 s (executor.py:17-25), so lock wait + long body can exceed 180 s and truncate the tick wait, escalation and park -- the interesting tail. stop_recording returns None when adb pull fails (adb_driver.py:1687-1689) and _capture only journals raised exceptions (dispatcher.py:85-92), so a recording that was never retrieved leaves no note.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented per the skeptic's sketch, with two deviations found necessary during implementation:

HALF 1 (timing): dispatcher.py's run_one no longer starts the recording at claim time. It sets
Executor.on_phone_acquired (a new one-shot hook, executor.py __init__) to a closure that calls
driver.start_recording(op_id); take_phone fires and clears that hook right after
stack.enter_context(self.driver.lock(**kw)) succeeds, before _recover_if_dirty -- so the 180s
budget now starts once the lock is genuinely this op's, never during lock-wait or on another
lane's screen.

HALF 2 (silent None): run_one's finally now calls driver.stop_recording(op_id) directly (not
through the return-value-discarding _capture) and journals a new "debug_recording_missing" note
(same shape as the existing debug_capture_failed) when the result is None.

DEVIATION 1, tracked start: run_one tracks a local `recording_started` flag (set True only when
the on_phone_acquired hook actually fires) and the finally block calls stop_recording only when
it is True. Without this, an op that is refused before it ever calls take_phone (e.g.
send_document's missing-file check, which raises before the ExitStack/take_phone) would still
call stop_recording every time debug_capture is on, and FakeDriver.stop_recording doesn't
distinguish "never started" from "started but lost" (it returns a fake path unconditionally
unless fail_recording is set) -- so this would have been untestable AND, against the real
AdbDriver (which does distinguish, returning None for an op it never recorded), would have
journalled a "debug_recording_missing" false positive on every ordinary "phone busy"
device_unavailable refusal, which take_phone's own docstring calls "the documented normal case,
not an edge". Adjusted the pre-existing test
test_debug_capture_on_a_refusal_takes_an_error_shot_not_a_post_shot accordingly (send_document's
refusal never touches the phone, so recording_started/stopped are now [] rather than [op_id]).

DEVIATION 2, hook cleanup: the finally block also resets executor.on_phone_acquired = None
whenever debug_capture is on, even if the hook never fired. Without this, a hook left set by an
op whose take_phone raised (device_unavailable) would dangle on the shared Executor instance and
could fire later for a completely unrelated take_phone call (IdentityWatcher, BroadcastRunner, or
the next op if debug_capture were toggled), starting a recording under the wrong op_id.

NOT ADDRESSED, said rather than silently narrowed: on_phone_acquired is one shared attribute on
the Executor, set by the dispatcher thread just before calling the dispatched method and
consumed inside take_phone on whichever thread reaches it first. Since IdentityWatcher and
BroadcastRunner call take_phone on their own threads and the reviewer's own TOCTOU argument for
Half 1 already establishes real contention exists, there is a narrow race: another thread could
in principle win the lock and consume/fire this op's hook before the dispatcher's own take_phone
call does, attributing the recording to the wrong window. This is unaddressed on purpose -- the
window is a few in-process instructions wide (governor.check/ledger.classify, no I/O), debug
capture is off by default, and closing it would need per-call hook passing through every one of
the four send verbs and four operations verbs, which is the "no per-method changes" blast radius
the reviewer's own sketch explicitly ruled out.

Tests: tests/test_bridge_executor.py -- added
test_debug_capture_recording_starts_only_after_the_lock_is_acquired (half 1, spies on
start_recording and asserts lock_events already contains "acquire" at the instant it fires;
fails before the fix with lock_events == [], passes after) and
test_a_lost_recording_pull_is_journalled_not_silent (half 2, fail_recording=True, asserts a
debug_recording_missing journal note names the op and the op itself still completes "done";
fails before the fix with no note, passes after). Adjusted
test_debug_capture_on_a_refusal_takes_an_error_shot_not_a_post_shot per deviation 1 above.
Verified both new tests and the adjusted one fail on the pre-fix code (git stash of just
bridge/dispatcher.py and bridge/executor.py) and pass after.

Ran: .venv/bin/python -m pytest tests/test_bridge_executor.py -q -> 162 passed (0 before this
task's changes were 159; +3 net: 2 new tests, 1 pre-existing test's assertion corrected).

Left at 180s screenrecord ceiling and TASK-228's truncated-tail acceptance untouched, as directed.
<!-- SECTION:NOTES:END -->
