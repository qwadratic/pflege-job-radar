---
id: TASK-264
title: >-
  The operator's own health command prints none of the liveness fields, and its
  counters reset on every restart
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 12:30'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 211000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: tools/wa_bridge.py:290. Severity: operator-blind. 

HOW IT HAPPENS: The inbound watcher thread is dead. A human runs the one command built to answer 'is the rail alive' and gets a green-looking report: version, rail number, queue, quota. The fact that would have told them is in --json only, and any repeat-incident counter has been zeroed by the restarts the incident caused.

WHAT IT COSTS: The manual fallback for the alarm that was never built hides exactly the fields the design names as decisive, and erases the counters that would reveal a repeating incident.

PROPOSED DIRECTION (not a decision): Print the facts the design already names: watcher last_ok_at and its age, every false `alive` flag, phone_ops depth and oldest age, inbound unacked count and oldest, retention held and when it last ran. Exit non-zero when any of them is bad so the command can be dropped into a timer later without rework. For the counters, report a windowed count from the journal rather than a process-lifetime integer, so a restart does not erase the history that matters.

VERIFICATION NOTES: cmd_health (tools/wa_bridge.py:290-318) prints version, rail number, driver kind, the outbound queue dict, quota, the media backlog lines and the identity watcher totals -- and nothing else. Verified absent from the default output: watcher.last_ok_at, any `alive` flag, ops_dispatcher, inbound backlog / oldest_unacked_at, retention. They exist only under --json. The counters are process-lifetime instance attributes set to 0 in __init__ (executor.py:109-118 for inbound_seen/unresolved/dirty_recovered, watcher.py:78 for idle_dirty_recovered), so a crash-looping executor always reports a clean history. The journal does record the underlying events (idle_dirty_recovered, dirty_state_recovered, watcher_error) and survives restarts, so the proposed windowed count is available.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reuse bridge/relay_pull.py's WATCHER_STALE_SEC/INBOUND_BACKLOG_STALE_SEC and bridge/ledger.py's age_sec (no new thresholds invented) to judge watcher/inbound staleness client-side in tools/wa_bridge.py cmd_health.
2. Add bridge/ledger.py::Ledger.event_count(event, start, end), a bounded-window journal count mirroring count_spent's shape.
3. Wire a new additive 'journal_recent' key into Executor.health() (idle_dirty_recovered/dirty_state_recovered/watcher_error over a 24h window, HEALTH_JOURNAL_WINDOW_SEC) -- the process-lifetime counters stay untouched since other code still reads them.
4. Rewrite cmd_health's text branch to always print watcher/ops_dispatcher/phone_ops/inbound/retention (the five fields named in the finding's VERIFICATION NOTES), and return EXIT_ATTENTION when watcher looks dead/stale, ops_dispatcher.alive is false, the inbound backlog is stale, or retention has errors. phone_ops depth/age is printed but not judged (no reviewed threshold exists for it, so none is invented).
5. Tests: tests/test_wa_bridge_cli.py (a healthy body prints clean and exits 0; a dead-watcher/stuck-dispatcher/stale-backlog/retention-error body prints ATTENTION and exits 1) and tests/test_bridge_executor.py (Ledger.event_count windowing, and health()['journal_recent'] surviving a restart that zeroes the instance counter).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented. Files: bridge/ledger.py (Ledger.event_count), bridge/executor.py (HEALTH_JOURNAL_WINDOW_SEC + health()['journal_recent']), tools/wa_bridge.py (cmd_health prints watcher/ops_dispatcher/phone_ops/inbound/retention every call, non-zero exit when watcher dead/stale, ops_dispatcher dead, inbound backlog stale past INBOUND_BACKLOG_STALE_SEC, or retention has errors -- thresholds reused from bridge/relay_pull.py, none invented). Scope kept to the five fields VERIFICATION NOTES names absent (watcher, ops_dispatcher, phone_ops, inbound.oldest_unacked_at, retention); did not add per-field alive checks for media_watcher/identity_watcher/reconcile_watcher/unresolved_send_watcher, which are already partially surfaced and outside what the finding verified as missing. Added tests/test_wa_bridge_cli.py::test_health_prints_the_liveness_fields_when_everything_is_alive and ::test_health_flags_a_dead_watcher_a_stuck_dispatcher_and_a_retention_error, and tests/test_bridge_executor.py::test_ledger_event_count_windows_the_journal_by_name_and_by_moment and ::test_health_reports_a_windowed_journal_count_that_survives_a_restart. Ran narrowly: tests/test_wa_bridge_cli.py (84 passed) and tests/test_bridge_executor.py (157 passed). Did not run the full suite per standing instruction (one full run happens in the separate verification pass). Left status In Progress and acceptance criteria unchecked per the dispatched task's own instruction -- that happens in verification.
<!-- SECTION:NOTES:END -->
