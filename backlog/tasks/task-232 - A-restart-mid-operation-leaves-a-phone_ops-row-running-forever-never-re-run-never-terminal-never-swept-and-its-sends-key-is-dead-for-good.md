---
id: TASK-232
title: >-
  A restart mid-operation leaves a phone_ops row 'running' forever: never
  re-run, never terminal, never swept -- and its send's key is dead for good
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-23 08:02'
updated_date: '2026-09-23 08:31'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 179000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Found independently by 2 review lenses. Location: bridge/ledger.py:540. Severity: loses-messages. 

HOW IT HAPPENS: systemd restarts pflege-wa-bridge (Restart=always, RestartSec=3) or the mini reboots while the dispatcher is inside a send. The phone_ops row stays `running` and is never claimed, never failed, never swept. The outbound row stays `attempting`; every catchup re-drive of that turn answers 504 send_unconfirmed. The candidate never gets that reply, and no failure record exists to look at.

WHAT IT COSTS: One permanently undelivered reply per restart-during-an-op, invisible: the op is not failed so there is nothing to review, the outbound row is never swept (resolved_at is null), and every screenshot and recording named after that op is held forever as 'op still running'.

PROPOSED DIRECTION (not a decision): Decide at start-up what a `running` row means and act on it once. For read-only kinds (read_thread, list_chats, reconcile) re-queuing is free. For a send, either fail the row with a distinct reason ('the executor restarted while this was in flight') so it becomes terminal and reviewable, or re-queue it and let first-body-wins decide -- but it must stop being a state nothing can leave. Pair it with a staleness rule for queued rows, so a reboot after an evening of downtime does not type three-hour-old replies into live conversations the moment the dispatcher returns.

VERIFICATION NOTES: claim_next_op selects state = queued only (ledger.py:540-556); nothing anywhere reads OP_RUNNING at start-up (grep for OP_RUNNING hits only its definition and claim_next_op itself); sweep deletes phone_ops only where finished_at is not null (ledger.py:1103-1105). retention.classify_op_artifact returns hold 'op still running' (retention.py:90) for every artefact of that op, forever. The outbound consequence is worse than the finder states: begin() leaves the row ATTEMPTING, ATTEMPTING is not in RESENDABLE (ledger.py:52), so classify() returns 'replay' and _replay_response RAISES send_unconfirmed for any later attempt on that deterministic key (executor.py:841-845). catchup.py re-drives the same turn with the same deterministic client_msg_id, so that reply is 504 forever until a human reconciles -- it is not merely 'not listed'.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Verify the sceptic's VERIFICATION NOTES directly against the code (claim_next_op only selects
   OP_QUEUED; sweep only deletes finished_at IS NOT NULL rows; retention.classify_op_artifact holds
   any non-terminal state forever; ATTEMPTING is not RESENDABLE so a restart-during-send 504-loops
   catchup forever). Confirmed independently -- proceed with the fix.
2. Add Ledger._recover_stuck_ops(now), called once from Ledger.__init__ right after the migrations
   commit (same convention as _migrate_phone_ops -- a one-time recovery pass a fresh Ledger runs on
   open). Any phone_ops row still 'running' belongs, by construction, to a dead process (one
   dispatcher thread per process) -- reuse the existing mark_op_failed(op_id, error, now) for each,
   with a distinct error code ('restarted_while_running') so the row becomes OP_FAILED: terminal,
   reviewable via the existing retention/resolve_op paths, and swept like any other failed op.
3. Scope: uniform mark_op_failed for every kind found running, not a re-queue for read-only kinds.
   send_photos/send_gallery/send_document/clear_chat/delete_chat have NO ledger idempotency
   (executor.py says so explicitly) -- re-queuing them risks a second real WhatsApp action on
   restart, which is worse than the bug this fixes. Uniform fail-and-record is the smaller, safer,
   single-code-path diff and satisfies the task's own AC ("must stop being a state nothing can
   leave") without guessing at a per-kind safety classification nobody asked for.
4. Does not touch the outbound ATTEMPTING/RESENDABLE state machine, executor.send()/classify()/
   _replay_response, or dispatcher.py/server.py -- Rule 4 (only reconcile resolves ATTEMPTING)
   stays exactly as designed.
5. Add one test in tests/test_bridge_executor.py (the file that already covers the phone-op FIFO
   queue / OpsDispatcher): claim a row on one Ledger (simulating the dispatcher having claimed it),
   close it without ever marking it done/failed (the crash), open a second Ledger on the same
   sqlite file (the restart), assert the row is OP_FAILED with the new error code. Verified this
   test fails on the pre-fix code and passes on the post-fix code.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
IMPLEMENTED. Independently re-verified the sceptic's VERIFICATION NOTES against the code before
touching anything (claim_next_op ledger.py:540-556 selects OP_QUEUED only; sweep ledger.py:1103-1105
deletes only finished_at IS NOT NULL rows; retention.classify_op_artifact retention.py:96-97 holds
any non-OP_DONE/OP_FAILED state forever; ATTEMPTING not in RESENDABLE ledger.py:52 so a restart-
during-send replays into send_unconfirmed via executor.py:831-845 and catchup.py re-drives it every
3 minutes forever). Confirmed independently -- the finding is real and the fix is proportionate.

Fix: bridge/ledger.py -- new Ledger._recover_stuck_ops(now), called once from __init__ right after
the schema/migrations commit (same one-time-recovery convention as _migrate_phone_ops). Any
phone_ops row still 'running' when a fresh Ledger opens belongs, by construction, to a dead process
(one dispatcher thread per process) -- it is passed to the existing mark_op_failed(op_id, error, now)
with a distinct error code {"code": "restarted_while_running", "http_status": 504, "retryable":
false}, so the row becomes OP_FAILED: terminal, visible over GET /v1/ops/<id>, reviewable via the
existing retention.classify_op_artifact OP_FAILED branch (sends resolve off their own client_msg_id;
the keyless kinds land in TASK-230's manual resolve_op path), and swept by the existing sweep() once
past retention.

Scope decision, deliberately narrower than the sketch: EVERY kind found 'running' is marked failed
uniformly, not just 'send'. The sketch's read-only/send split (re-queue read_thread/reconcile,
fail the rest) was not implemented -- send_photos/send_gallery/send_document/clear_chat/delete_chat
have NO ledger idempotency of their own (executor.py's own docstrings on send_photos/gallery/
document say so explicitly: "a retried call sends the photos again, full stop"), so silently
re-queuing any of those on restart would let a resurrected op re-run a real WhatsApp send or a
destructive chat action a second time -- strictly worse than the stuck-forever bug this task is
about. Uniform fail-and-record is the smaller, single-code-path fix, satisfies AC#1 as written
("stop being a state nothing can leave") without inventing a per-kind safety classification the
task did not ask for, and does not foreclose a later, separate decision to re-queue the genuinely
read-only kinds if that is wanted.

Does NOT touch: the outbound ATTEMPTING/RESENDABLE state machine, executor.send()/classify()/
_replay_response, bridge/dispatcher.py, bridge/server.py. Rule 4 (only reconcile resolves
ATTEMPTING) is untouched, as the sketch required. The "staleness rule for stale queued rows"
mentioned in the task's own PROPOSED DIRECTION is a separate, later decision and was not bundled in.

Noted for the verification pass, not acted on: TASK-248 and TASK-252 are near-duplicate findings
of this exact bug from the same critique run (different severities/lenses). This fix resolves the
'running' half of both. TASK-248 additionally wants queue-depth/oldest-age in the heartbeat and a
staleness TTL for queued rows; TASK-252 wants phone_ops state counts in /v1/health -- neither is
touched here (out of TASK-232's scope; those tasks' own AC will need re-checking against this fix
when they come up).

Test: tests/test_bridge_executor.py::test_a_restart_mid_op_unsticks_the_running_row_it_left_behind
(TASK-232 section, next to the existing phone-op FIFO queue / OpsDispatcher tests). Claims a
phone_ops row on one Ledger (simulating the dispatcher mid-op), closes that Ledger without ever
calling mark_op_done/mark_op_failed (the crash), opens a second Ledger against the same sqlite file
(the restart), and asserts the row reached OP_FAILED with the new error code. Verified by hand:
fails on pre-fix ledger.py ("AssertionError: assert 'running' == 'failed'"), passes on post-fix.

Ran narrow: .venv/bin/python -m pytest tests/test_bridge_executor.py -q -> 125 passed.
Also ran tests/test_bridge_retention.py -q (shares the phone_ops table's semantics) -> 23 passed,
as a sanity check only -- not the full suite; that is the owner's single verification pass.

Not committed -- leaving the diff for review.
<!-- SECTION:NOTES:END -->
