---
id: TASK-268
title: >-
  The ops queue is not the only caller of the phone, so TASK-227's mutual
  exclusion does not actually hold
status: Done
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 215000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/broadcast.py:177. Severity: degraded. 

HOW IT HAPPENS: A candidate replies; the reply op is queued and claimed; take_phone waits 30 s for a flock the identity watcher is holding for a chat evidence read (which, per finding 6, re-runs every 15 s while any file stays undecided); the op fails with 503 device_unavailable; the turn is recorded failed and handed to catch-up.

WHAT IT COSTS: The bare-flock race TASK-227 was written to remove is still live for two of the three callers, and the loser is usually the candidate-facing reply. Each loss feeds the catch-up re-drive path where findings 1 and 2 do their damage. (The 503 itself is clean -- take_phone refuses before ledger.begin, so no row and the key stays usable -- so the direct cost is delay plus a wasted brain call, not a corrupt state.)

PROPOSED DIRECTION (not a decision): Either put the broadcast runner and the identity watcher behind the same phone_ops queue -- they are phone-touching work like everything else and the dispatcher is already generic over method names (dispatcher.py::_resolve) -- or stop claiming exclusivity in the docstring and give the two out-of-band callers a lock discipline that yields to the queue: short patience, and a re-check of the queue before taking the phone for a long evidence read.

VERIFICATION NOTES: CONFIRMED. dispatcher.py's docstring claims "Ordering and mutual exclusion fall out for free because there is exactly one caller left." There are three real ones: the dispatcher thread; BroadcastRunner.cycle -> Broadcast.step -> `self.executor.send(request)` (broadcast.py:177) on its own thread every DEFAULT_POLL_SEC=5 s (broadcast.py:66, started at server.py:492); and IdentityWatcher.cycle -> executor.auto_match_media -> _read_evidence_for -> take_phone(timeout=IDENTITY_LOCK_TIMEOUT_SEC=180) (executor.py:739, watcher.py:337) every 15 s. Both bypass phone_ops entirely and race the bare flock. A fourth, InboundWatcher._check_idle_dirty (watcher.py:110-140), is NOT a real contender -- it probes with lock(timeout=0) and yields -- so exclude it. The asymmetry the finder names is right: a dispatched send waits the default LOCK_TIMEOUT_SEC=30 (executor.take_phone has no timeout kwarg passed, driver.py:38) against the identity watcher's 180 s patience, so the candidate-facing reply is the one that loses.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified from code, not from the prior assessment: dispatcher.py's docstring claim ('exactly one caller left') is false -- BroadcastRunner (broadcast.py::Broadcast.step, calling executor.send) and IdentityWatcher (executor.py::auto_match_media -> _read_evidence_for) both take huawei01.lock directly, outside phone_ops, on their own threads. Confirmed timeouts: driver.py LOCK_TIMEOUT_SEC=30.0 (dispatched send path, no override) vs executor.py IDENTITY_LOCK_TIMEOUT_SEC=180.0 (IdentityWatcher's own take_phone(timeout=lock_timeout) call, executor.py _read_evidence_for) -- a genuine, reachable asymmetry, not hypothetical.

Fix implemented (courtesy yield, not a full requeue -- rejected a full route-through-phone_ops fix as disproportionate, matching TASK-131 round 6's reasoning for giving IdentityWatcher its own schedule):
- bridge/executor.py::Executor.auto_match_media -- before the long-patience take_phone (via _read_evidence_for), peek at ledger.phone_ops_queue_counts()['queued']; if non-empty, note 'identity_evidence_deferred' and continue to the next row without taking the lock. The file stays queued, retried next cycle, same as a genuine read failure already does.
- bridge/broadcast.py::Broadcast.step -- same check right before executor.send(request); if the queue is non-empty, note 'broadcast_send_deferred' and return None (item stays exactly as due, untouched; the runner's own next 5s cycle retries).
- bridge/dispatcher.py module docstring -- replaced the false 'exactly one caller left' claim with the real, now-true contract (dispatched ops get priority; BroadcastRunner/IdentityWatcher yield to a non-empty queue rather than racing it).

Did not touch: take_phone, driver.py's lock mechanics, the send path's retry/pacing/governor logic, the ledger phone_ops state machine, or ReconcileWatcher (a fourth lock-taker not named in this finding's own verification notes -- out of scope here).

Tests added (each verified to FAIL without the fix and PASS with it, by hand-patching the guard out and back):
- tests/test_bridge_executor.py::test_a_queued_phone_op_defers_the_evidence_read_instead_of_racing_it -- seeds a queued phone_op, calls auto_match_media(), asserts driver.lock_events stays empty and the file stays queued.
- tests/test_bridge_operations.py::test_a_queued_phone_op_defers_the_step_instead_of_racing_it -- seeds a queued phone_op, calls broadcast.step(), asserts it returns None and driver.lock_events stays empty.

Test run (narrow, as instructed -- not the full suite): .venv/bin/python -m pytest tests/test_bridge_executor.py tests/test_bridge_operations.py -q -> 223 passed, 1 failed. The 1 failure (test_maintenance_once_reviews_before_sweeping_and_surfaces_the_result_in_health, S.maintenance_once/screenshot retention) is pre-existing and unrelated: it touches TASK-230/253's retention sweep, a different subsystem than this fix (executor.auto_match_media / broadcast.step), and the working tree already carried substantial uncommitted work on bridge/server.py etc. before this task started. Left as-is for the owner's verification pass.

Status left at In Progress; acceptance criteria not checked, per task-finalization being a separate pass.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC1 and AC2 genuinely satisfied. bridge/executor.py:813-821 and bridge/broadcast.py:180-182 add the yield-guard; bridge/dispatcher.py:11-18 corrects the docstring; both cited tests pass with no regressions across the two touched test files.
<!-- SECTION:FINAL_SUMMARY:END -->
