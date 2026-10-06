---
id: TASK-248
title: >-
  A restart strands 'running' ops forever and lets stale 'queued' ops type old
  replies after the outage
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - wrong-answer-to-candidate
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 195000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/ledger.py:546. Severity: wrong-answer-to-candidate. 

HOW IT HAPPENS: Bridge restarts (deploy, OOM, wedged adb) while an op is mid-send: that row stays 'running' forever, its screenshots and mp4 are held forever, the VPS client polls to its deadline and raises answer_timeout. Separately: Luna queues a reply at 23:00; the bridge is down for deploy until 23:40; the VPS gave up at 23:02 and the candidate has since sent a new message that opened a new turn with a new turn_key (hence a new reply_key, so first-body-wins does not cover it). At 23:40 the dispatcher drains oldest-first and types the 23:00 reply after the 23:40 one.

WHAT IT COSTS: Stuck 'running' rows are invisible (no queue depth or oldest-op age anywhere in the heartbeat) and pin debug artefacts in the held pile forever. Stale queued ops send a real person an answer to a question they moved on from, out of order, with no human in the loop -- and for send_photos/gallery/document, clear_chat and delete_chat a resurrected op re-runs the whole action, including a destructive one an operator already re-issued by hand after the first attempt appeared to fail.

PROPOSED DIRECTION (not a decision): On startup, sweep phone_ops for rows left in 'running' and resolve each to a terminal state that says 'the process died mid-op, the handset may have been touched' -- they are the exact analogue of an 'attempting' outbound row, so neither silently re-running them nor leaving them to rot is acceptable. For queued rows, pick an age past which a ticket is refused rather than executed (same TTL as the previous finding's cancel). Put queue depth and oldest-queued-age into OpsDispatcher.heartbeat so /v1/health can show a backlog forming instead of only whether the thread is alive.

VERIFICATION NOTES: CONFIRMED on every leg. claim_next_op (ledger.py:540-556) selects `where state = 'queued' order by position limit 1` and nothing else in the package ever re-reads an OP_RUNNING row -- I read all of server.py::main (lines 444-534) and there is no startup recovery pass. ledger.sweep (1077-1104) deletes phone_ops only `where finished_at is not null`, and mark_op_done/mark_op_failed are the only writers of finished_at, so a stranded running row is never collected. retention.classify_op_artifact (retention.py:94) returns ('hold', 'op still running') for every artefact naming it, permanently. The queued half is equally confirmed: enqueue_op writes a durable sqlite row with no expiry, and the dispatcher drains oldest-first by position on restart. OpsDispatcher.heartbeat (dispatcher.py:165-168) reports only poll_interval_sec, alive and debug_capture -- no depth, no oldest-age. The non-idempotent kinds named are right: send_photos/send_gallery/send_document are documented as having no ledger idempotency (executor.py:232-240), and clear_chat/delete_chat re-run their whole destructive walk.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
This is an unusual case: TASK-248's own ticket has zero implementation notes and Status='To Do', so nobody formally closed it -- but reading bridge/ledger.py (_recover_stuck_ops line 467, claim_next_op's budget expiry line 726, phone_ops_queue_counts line 827) and app/wa/bridge.py's unconditional OP_BUDGET_HEADER (line 464) shows every sub-claim of the finding is genuinely already fixed by sibling tasks TASK-232/TASK-243, with passing regression tests (test_a_restart_mid_op_unsticks_the_running_row_it_left_behind, test_claim_next_op_expires_a_stale_row_and_serves_the_fresh_one_behind_it). Recommend the owner formally close TASK-248 referencing TASK-232/TASK-243 rather than leave it dangling as 'To Do', but the acceptance criteria are substantively met.
<!-- SECTION:FINAL_SUMMARY:END -->
