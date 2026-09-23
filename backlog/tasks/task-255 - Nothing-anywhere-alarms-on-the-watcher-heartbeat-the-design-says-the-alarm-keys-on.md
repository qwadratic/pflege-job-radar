---
id: TASK-255
title: >-
  Nothing anywhere alarms on the watcher heartbeat the design says the alarm
  keys on
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 11:39'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 202000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/watcher.py:24. Severity: operator-blind. A comment in the code already states this limit -- the question is whether that trade is still acceptable now the rail carries live conversations. 

HOW IT HAPPENS: adb disconnects, the USB cable loosens, the phone reboots, or WhatsApp's notification access is revoked by an update. InboundWatcher raises DriverError on every 5 s cycle, increments errors, journals watcher_error — and nobody is told. The relay keeps logging 'cursor at N' against an outbox that is legitimately empty because nothing can be captured.

WHAT IT COSTS: The rail can be blind for hours while every dashboard is green — the exact signature of the TASK-225 incident that sat for 45+ minutes. The counters exist; the thing that looks at them does not. Two code comments claim otherwise.

PROPOSED DIRECTION (not a decision): Put the check on the side already awake and already reaching the mini: Relay.run() can call health() on a slow cadence and log loudly (or write a flag the /wa/threads view surfaces) when last_ok_at, inbound_backlog.oldest_unacked_at or any watcher's alive flag goes wrong. No new transport, no new credential. Until it exists, fix watcher.py:24 and server.py:5 to say what is actually true.

VERIFICATION NOTES: CONFIRMED. I grepped every reader of last_ok_at and /v1/health outside the executor's own health() and tests: Relay.run() (relay_pull.py:316-333) calls drain_once only and never health(); health() is reached solely from the manual --probe path (:374); deploy/ contains no timer or service that polls /v1/health or /wa/bridge-health (the only units are catchup, followups, purge-test, known-phones-export, hunter, web, wa, and the three bridge units). ADMISSION NUANCE: the finder's 'already admitted' is right in the backlog — TASK-132 (the health timer with the conjunction alert) is status 'To Do', and TASK-225 AC4 is explicitly unchecked with a note saying the capture canary 'has not been built'. But the CODE does not admit it: watcher.py:24 asserts 'it is what the health alarm keys on' and server.py:5 calls /v1/health 'what the 3-minute timer alarms on (TASK-132)'. Those are false statements about a system that has no such alarm, and the next reader will believe them. Given the rail now carries live conversations and three of the findings above are silent-loss modes whose only possible signal is this heartbeat, the trade is no longer acceptable.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented both tiers of the sceptic's plan, scoped to TASK-255 only.

Tier 1 (truth fix): reworded bridge/watcher.py:23-24 and bridge/server.py:5 -- both now state that last_ok_at/GET /v1/health is what a health alarm has to key on / is polled by, not that TASK-132's 3-minute conjunction-alert timer exists (it is still To Do). Comment-only, no behaviour change.

Tier 2 (the log-loudly check): added Relay.check_watcher_alarm() to bridge/relay_pull.py, wired into Relay.run() on a new ALARM_CHECK_INTERVAL_SEC=60s cadence (separate from the drain's own INTERVAL_SEC, so the alarm's own health() call -- up to HEALTH_TIMEOUT_SEC=75s when the phone is genuinely gone -- can't dominate the drain loop). It calls the existing executor.health(), and logs a grep-able 'ALARM: watcher heartbeat looks dead -- ...' line via self.log when: watcher.alive is false, watcher.last_ok_at is older than WATCHER_STALE_SEC=60s (or never set), or inbound.oldest_unacked_at is older than INBOUND_BACKLOG_STALE_SEC=60s. All three thresholds are named in the code as first numbers to make the alarm exist at all, not reviewed ones -- TASK-132 (still To Do) is where a considered number and a real alert channel belong; said so in the comments rather than pretending these are final. Never raises past itself (a broken health() call is itself logged as an ALARM line, not a crash).

Did not touch Cursor/advance/drain_once/deliver, the ledger, or any send-path code -- confined to bridge/relay_pull.py plus the two comment lines, as scoped.

Test: tests/test_bridge_relay.py, 6 new tests -- test_check_watcher_alarm_is_silent_on_a_healthy_body, test_check_watcher_alarm_fires_on_a_stale_last_ok_at, test_check_watcher_alarm_fires_when_alive_is_false, test_check_watcher_alarm_fires_on_an_old_inbound_backlog, test_check_watcher_alarm_never_raises_when_health_itself_fails, and test_relay_run_asks_the_watcher_alarm_check_on_its_own_cadence (drives Relay.run() for exactly one loop pass via a fake stop event and proves check_watcher_alarm is actually called from run(), not just defined -- this is the one that would have failed outright before the fix, since neither the method nor the call in run() existed). All fail without the fix (AttributeError or assertion failure) and pass with it. Ran narrowly: .venv/bin/python -m pytest tests/test_bridge_relay.py -q -> 38 passed. Also ran tests/test_bridge_executor.py (imports bridge.watcher) as a side-check on the comment-only edit -> 150 passed. Did not run the full suite per standing instruction.

Left at In Progress; did not check acceptance criteria or write a final summary -- that's the verification pass, not this one. Not committed.
<!-- SECTION:NOTES:END -->
