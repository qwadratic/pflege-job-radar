---
id: TASK-242
title: >-
  A ledger write error kills the ops dispatcher thread for good, and the whole
  rail silently stops sending
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 189000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/dispatcher.py:133. Severity: loses-messages. 

HOW IT HAPPENS: Ledger write fails once (disk full on the mini, disk I/O error, or an op result that json.dumps cannot serialise). mark_op_done/mark_op_failed raises, the exception unwinds cycle() and run(), the dispatcher thread exits. Every phone-touching route keeps answering 200 {state: queued} and keeps inserting phone_ops rows nobody drains.

WHAT IT COSTS: Every outbound reply to every candidate stops. The VPS client polls to its budget and raises answer_timeout/UNCERTAIN for each one; inbound keeps arriving and piling up; the process, the HTTP surface and all three watchers still look healthy. Recovery needs a human restart.

PROPOSED DIRECTION (not a decision): Guard cycle() the way the watchers guard theirs (claim + run + outcome write inside one try that counts an error, logs, and retries next tick), and make run() the last line of defence. Alarm on ops_dispatcher.alive=false together with a non-empty queued count, since nothing reads that boolean today.

VERIFICATION NOTES: Confirmed by reading the code. run_one() (dispatcher.py:108-138) guards only `method(**row['args'])`; mark_op_done (133), both mark_op_failed calls (120, 128) and claim_next_op inside cycle() (143) are unguarded, and run() (149-152) has no try. Contrast bridge/watcher.py:95, 216, 338 -- all three watchers catch Exception inside their own cycle() and keep counting. run_one's own docstring says 'Never raises', which is false for exactly the three outcome writes. Ledger is one sqlite connection (ledger.py:286, check_same_thread=False) and bridge/server.py:453 is the only process that opens it, so SQLITE_BUSY contention from another process is NOT a trigger -- the real triggers are a full disk / disk I/O error (plausible precisely because of finding 3) and any json.dumps TypeError on an op result. Downstream chain verified: app/wa/bridge.py:473 -> _await_op (476-503) polls GET /v1/ops/<id> and raises BridgeError(answer_timeout, UNCERTAIN) at the deadline, so every queued reply dies that way. Two corrections to the finder: (a) it is not the only trace -- an uncaught exception on a threading.Thread prints a traceback to stderr, i.e. journald gets it; (b) nothing in tools/ or app/ reads ops_dispatcher.alive, so the health boolean is indeed unalarmed (grep: only bridge/executor.py:785 writes it).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented the fix in bridge/dispatcher.py: OpsDispatcher.run_one's resolve->call->write-outcome
sequence is now wrapped in an outer try/except Exception (structurally the only way to catch a
failure from mark_op_done/mark_op_failed, since an exception raised inside an except/else clause
is never caught by that same try's own except). OpsDispatcher.cycle() now guards
ledger.claim_next_op() the same way. Both catches count into new self.errors/self.last_error/
self.last_error_at fields (mirroring bridge/watcher.py's watchers) and log via self._log instead of
propagating; the op row is left in whatever state the failed write left it in (still `running`) and
the next poll moves on -- no retry-in-place, no resend, matching the sketch. heartbeat() now
surfaces errors/last_error/last_error_at alongside the existing alive/poll_interval_sec/
debug_capture fields.

Did not touch bridge/server.py routes, app/wa/bridge.py, or ledger.py's schema/state machine.
Did not wire an alarm on ops_dispatcher.alive or the new error fields -- that is TASK-132's surface,
left alone per the task's own scope note.

Test added: tests/test_bridge_executor.py
  - test_a_ledger_write_failure_marking_an_op_done_is_counted_not_fatal -- patches
    rig.ledger.mark_op_done to raise sqlite3.OperationalError for one specific op_id, enqueues that
    op plus an unrelated one, drives dispatch.cycle() twice. Verified this raises out of cycle() on
    stock dispatcher.py (reverted the fix locally with `git stash push -- bridge/dispatcher.py`, ran
    the test, saw it fail with the OperationalError propagating through cycle()/run_one(), then
    restored with `git stash pop`). With the fix: both cycle() calls return True, the bad op stays
    `running` (op_status), the good op reaches `done`, dispatch.errors == 1, last_error names the
    OperationalError.
  - test_a_claim_next_op_failure_is_counted_and_the_cycle_returns_false_not_fatal -- same proof for
    claim_next_op(), using a bare BrokenLedger stand-in (same pattern as the existing
    test_the_unresolved_send_watcher_error_is_counted_and_journalled_and_never_raises).

Ran only tests/test_bridge_executor.py (the file that already covers OpsDispatcher): 139 passed,
0 failed. Did not run the full suite, per instructions -- that is the owner's verification pass.

Not committed; left for the owner to review the diff.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
bridge/dispatcher.py:129-227 (outer try/except around run_one and cycle) and :252-258 (heartbeat surfacing errors), committed at 3578e72. Tests at tests/test_bridge_executor.py:2016 and :2051, run directly and confirmed passing.
<!-- SECTION:FINAL_SUMMARY:END -->
