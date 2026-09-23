---
id: TASK-260
title: >-
  The one thread that can touch the phone has a heartbeat with no progress
  evidence, and an unguarded loop that one sqlite error ends
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 12:02'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 207000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/dispatcher.py:165. Severity: operator-blind. 

HOW IT HAPPENS: A sqlite error inside mark_op_done ends the dispatcher thread. systemd sees nothing. Every phone-touching route still answers 200 queued; every op sits queued forever; each of Luna's sends spends its whole budget and comes back 'uncertain'. The outbound `queue` block in health stops growing, which is exactly what a quiet evening looks like.

WHAT IT COSTS: The rail stops sending while reporting itself alive, and the two numbers that would show it -- phone_ops depth and the age of the oldest queued row -- exist in no surface: not health, not the CLI, not a route.

PROPOSED DIRECTION (not a decision): Give the dispatcher the watchers' heartbeat shape (cycles, errors, last_ok_at, last_error) plus queued-count and oldest-queued-age from a phone_ops version of queue_counts. Guard the loop the way run_one already guards the job: a failure to claim or to write an outcome is counted and retried, not the end of the thread. Worth deciding at the same time whether a phone-touching route should refuse rather than enqueue when the dispatcher is not alive -- answering 'queued' to a queue with no drainer is the one case where the wire's 'queued' really is the lie server.py's docstring warns about.

VERIFICATION NOTES: heartbeat() returns exactly {poll_interval_sec, alive, debug_capture} (dispatcher.py:165-168) -- no cycles, no last_ok_at, no current op, no queue depth. health()['queue'] is ledger.queue_counts(), which groups the OUTBOUND table only (ledger.py:520-522); there is no phone_ops equivalent and phone_ops depth appears nowhere in health. cycle()'s claim_next_op (dispatcher.py:143) is unguarded, and mark_op_done (line 133, in the else clause) / mark_op_failed (lines 120 and 128, inside the except handlers) are all outside run_one's try body, so a sqlite failure in any of them propagates out of run_one -> cycle -> run and ends the thread, despite run_one's 'Never raises' docstring. Routes keep enqueuing and answering 200 {'state':'queued'} with no aliveness check. Client._await_op (app/wa/bridge.py:476-503) then burns the full budget and raises BridgeError CODE_ANSWER_TIMEOUT at UNCERTAIN_STATUS, exactly as claimed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Sceptic review split TASK-260 into two claims; verified both against the actual working tree
(not just committed HEAD) before acting.

Claim A (unguarded loop that one sqlite error ends): CONFIRMED STALE. The working tree already
carries an uncommitted TASK-242 fix that wraps run_one's resolve->call->write-outcome sequence in
an outer try/except, guards cycle()'s claim_next_op the same way, and had already added
errors/last_error/last_error_at to heartbeat(). Re-verified by reading bridge/dispatcher.py as it
stands (not reapplying anything) and running
`.venv/bin/python -m pytest tests/test_bridge_executor.py -k ledger_write_failure_marking_an_op_done_is_counted_not_fatal -q`
-> 1 passed. Left this half untouched, per the sceptic's own scope note.

Claim B (heartbeat with no progress evidence, no queue-depth/oldest-queued-age surface): CONFIRMED
REAL and still true after TASK-242's fix. Fixed:
- bridge/dispatcher.py: OpsDispatcher.__init__ gains self.cycles=0 and self.last_ok_at=None;
  cycle() increments self.cycles unconditionally at the top and sets self.last_ok_at after
  claim_next_op() succeeds (whether the queue was empty or a job ran), mirroring
  bridge/watcher.py's InboundWatcher.cycle(). heartbeat() now returns cycles/last_ok_at alongside
  the existing fields.
- bridge/ledger.py: added Ledger.phone_ops_queue_counts() -> {"queued": n, "oldest_queued_at": ts}
  via `select count(*), min(created_at) from phone_ops where state = 'queued'`, mirroring
  inbound_backlog()'s shape (raw timestamp, not a computed age -- same division of labour as that
  method, where relay_pull.py computes age from the timestamp it returns).
- bridge/executor.py: health() gains a new top-level "phone_ops" key set to
  ledger.phone_ops_queue_counts(), so tools/wa_bridge.py's --json path exposes it immediately.
  Left the default (non-json) CLI output alone -- printing it there is TASK-264's job.

Did not touch run_one's/cycle's exception guards, the send path, the phone_ops state machine, or
any write path. Did not decide whether a phone-touching route should refuse when the dispatcher is
not alive -- the task itself flags that as "not a decision" and it would touch route/send-path
behaviour, out of scope for this finding.

Tests added in tests/test_bridge_executor.py (TASK-260 section, after the TASK-242 tests):
- test_dispatcher_heartbeat_counts_cycles_and_records_last_ok_at_on_both_empty_and_job_cycles
- test_health_reports_phone_ops_queue_depth_and_oldest_queued_at_when_the_dispatcher_is_not_draining
Verified both fail on the pre-fix code: stashed bridge/dispatcher.py, bridge/ledger.py,
bridge/executor.py (reverting to committed HEAD, which predates even TASK-242), ran just these two
tests -> both failed with KeyError ("cycles", then "phone_ops"), then restored the fix and
confirmed both pass.

Ran only tests/test_bridge_executor.py (the file that already covers OpsDispatcher and
Executor.health()): 152 passed, 0 failed. Did not run the full suite -- that is the owner's
verification pass.

Not committed; left for the owner to review the diff. Status left at In Progress, acceptance
criteria left unchecked, per instructions -- finalization happens in the verification pass.
<!-- SECTION:NOTES:END -->
