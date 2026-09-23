---
id: TASK-261
title: >-
  Sends stuck unconfirmed/attempting can be counted but not listed:
  Ledger.unresolved() has no caller and no route
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 12:20'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 208000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/ledger.py:487. Severity: operator-blind. 

HOW IT HAPPENS: A send types the body and no tick appears -- the measured case, 2 of 23 live sends (executor.py:186-190). The row goes unconfirmed; health shows queue: {'sent': 41, 'unconfirmed': 2}. Recovering it needs the client_msg_ids, which no route, CLI command or health field will print, so in practice the rows sit until a human opens sqlite on the colleague's mini.

WHAT IT COSTS: The rail's own documented recovery path cannot be started from the operator's own tools, and each stuck row also pins its escalation screenshot in 'held' until the outbound row ages out at day 30 -- at which point the artefact becomes permanently held (finding 5).

PROPOSED DIRECTION (not a decision): Expose what already exists: one read-only route backed by Ledger.unresolved(), and a CLI command that prints the rows with their ages and feeds the ids straight into `reconcile`. Since the count is already in health, add the age of the oldest unresolved row too -- 'two unconfirmed' and 'two unconfirmed, oldest six days' are different information.

VERIFICATION NOTES: `unresolved()` is defined at ledger.py:487-493 and called from nowhere: the only other occurrence in the repo is a comment in retention.py:41. Recovery genuinely requires the ids: cmd_reconcile refuses without --keys (tools/wa_bridge.py:681-683) and the parser marks --keys required (line 760); /v1/reconcile takes client_msg_ids; health carries only queue_counts (state -> count), no ids and no ages. The TASK-230 pinning is real: an unconfirmed row keeps its escalation shot in 'held' via classify_escalation_shot (retention.py:112-124).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Verify TASK-235 is already in the working tree (UnresolvedSendWatcher, wired into server.main, tested) -- it is, so recovery already self-starts; this task is scoped to observability only.
2. bridge/executor.py: add Executor.unresolved_sends() wrapping ledger.unresolved(), mapping each Entry to {client_msg_id, thread_tag, state, age_sec} (mirrors unresolved_media()). Add oldest_unresolved_sec to health() next to queue_counts().
3. bridge/server.py: add GET /v1/unresolved route beside /v1/audit, plus a docstring line.
4. app/wa/bridge.py: add UNRESOLVED_PATH + Client.unresolved_sends() (needed for the CLI to reach the new route, mirrors unresolved_media()).
5. tools/wa_bridge.py: add unresolved-list subcommand (mirrors media-list/cmd_audit, prints id/state/thread/age, --json, a reconcile --keys hint) and a cmd_health line printed when queue has unconfirmed/attempting rows.
6. Tests: executor-level (unresolved_sends facts), HTTP route-level (GET /v1/unresolved), health (oldest_unresolved_sec), CLI (unresolved-list text/json/empty, health line present/absent) -- in tests/test_bridge_executor.py and tests/test_wa_bridge_cli.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented. Verified the task's own headline claim is stale: bridge/watcher.py::UnresolvedSendWatcher (TASK-235) is already in the working tree, wired into bridge/server.py::main() (started + heartbeat in health()), calls ledger.unresolved() every 60s and enqueues a real reconcile op for every stuck row -- confirmed via tests/test_bridge_executor.py's TASK-235 section (test_the_unresolved_send_watcher_queues_and_the_dispatcher_resolves_it et al., all passing). So 'no caller' is false and auto-recovery already runs unattended.

What was still true and is now fixed: no operator-facing way to SEE which rows are stuck -- confirmed no /v1/unresolved route existed (grepped every GET in bridge/server.py) and no CLI listing command existed (grepped every subcommand in tools/wa_bridge.py); health() carried only the bare queue count, no ids, no ages.

Added, read-only, additive, no send-path/state-machine changes:
- bridge/executor.py: Executor.unresolved_sends() (wraps ledger.unresolved(), ages via L.age_sec against self.clock()), oldest_unresolved_sec added to health() next to queue_counts.
- bridge/server.py: GET /v1/unresolved route (same shape as /v1/audit) + docstring line.
- app/wa/bridge.py: UNRESOLVED_PATH + Client.unresolved_sends() (the CLI needs this to reach the route; not in the sceptic's blast-radius list but required plumbing, same shape as unresolved_media()).
- tools/wa_bridge.py: unresolved-list subcommand (id/state/thread/age, --json, prints a 'reconcile --keys <ids>' hint) and a cmd_health line ('unresolved sends: N (oldest Xs) -- see `unresolved-list`') printed only when queue has attempting/unconfirmed rows.

Tests added (all fail on main without the fix, all pass with it): tests/test_bridge_executor.py -- test_unresolved_sends_lists_id_thread_state_and_age, test_get_v1_unresolved_returns_the_stuck_rows_id_and_age, test_health_reports_oldest_unresolved_sec_when_a_row_is_stuck; tests/test_wa_bridge_cli.py -- test_unresolved_list_prints_id_state_thread_and_age, test_unresolved_list_json_passes_the_executors_answer_through, test_unresolved_list_with_nothing_stuck_prints_no_reconcile_hint, test_health_says_unresolved_sends_out_loud_when_the_queue_has_them, test_health_says_nothing_about_unresolved_sends_when_the_queue_has_none.

Ran narrowly: tests/test_bridge_executor.py + tests/test_wa_bridge_cli.py + tests/test_wa_bridge_client.py (304 passed). Did not run the full suite (owner's own verification pass does that). Not committed -- owner reviews the diff.

Left at In Progress per instructions; acceptance criteria not checked here, that happens in the verification pass.
<!-- SECTION:NOTES:END -->
