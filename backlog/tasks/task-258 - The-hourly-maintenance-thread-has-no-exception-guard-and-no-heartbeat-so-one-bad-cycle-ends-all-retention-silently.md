---
id: TASK-258
title: >-
  The hourly maintenance thread has no exception guard and no heartbeat, so one
  bad cycle ends all retention silently
status: Done
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 205000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/server.py:411. Severity: operator-blind. 

HOW IT HAPPENS: An operator deletes or moves old shots out of WA_BRIDGE_STATE/shots by hand to free disk while the hourly sweep is between sorted(rglob) and stat(). FileNotFoundError propagates out of list_screenshot_candidates, out of maintenance_once, out of maintenance_loop; the thread exits and nothing restarts it. /v1/health keeps reporting last_retention from the last successful cycle, with no timestamp and no liveness flag for this thread.

WHAT IT COSTS: Ledger sweeping and artefact review both stop for the life of the process and the health body actively hides it by reporting a stale success. With debug capture on by default, shots and recordings then grow until someone notices the disk. The 'a dead X must not look like a quiet X' principle this package states in five other places is the one thing missing here.

PROPOSED DIRECTION (not a decision): Give maintenance_loop the same never-raises-per-cycle contract the watchers have, and a heartbeat in /v1/health with cycles, errors and last_ok_at so a dead sweep looks different from a quiet one. Stamp last_retention with the time it was computed, not just its counts, so the stale-value case is readable even without the heartbeat. Making list_screenshot_candidates tolerate a file that vanished between the listing and the stat is worth doing on its own, but it is the smaller half.

VERIFICATION NOTES: REAL in substance, but the finder's specific trigger is BLOCKED and I have corrected it. maintenance_loop (server.py:410-412) is confirmed a bare `while not stop.wait(interval): maintenance_once(executor)` with no try/except and no counters -- the only thread in the package without the never-raises-per-cycle envelope and the heartbeat every other thread has (InboundWatcher, MediaWatcher, IdentityWatcher, BroadcastRunner, OpsDispatcher all have both, and executor.health() at executor.py:775+ exposes them). executor.last_retention is set only on a successful cycle (server.py:403) and carries counts with no timestamp, so /v1/health keeps showing a plausible retention block after the thread is gone. BUT the named race cannot fire: Adb.screenshot (adb_driver.py:571-576) opens the path 'wb' and Adb.downscale (465-476) runs `convert path -resize path` -- neither unlinks, so the file never stops existing during a debug_shot, and the only caller of delete_paths is the maintenance thread itself. A reachable trigger does exist and is closer to how this machine is actually run: the sweep does `[p for p in sorted(shots_dir.rglob('*.png')) if p.stat().st_mtime < cutoff]` (adb_driver.py:1638-1640) -- rglob is fully materialised by sorted() before the first stat(), so anything that removes a file in that window (an operator clearing space on the mini by hand, which is standing practice there) raises FileNotFoundError out of the comprehension, out of maintenance_once, out of the loop.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
TASK-258 itself was never touched (still Status: To Do, no Implementation Notes, no Updated timestamp) -- the fix lives entirely under TASK-253's implementation notes for the identical location/trigger. The code and a passing regression test (tests/test_bridge_executor.py:1467-1495) genuinely satisfy both ACs, but closing TASK-258 should record that it is a duplicate of TASK-253 rather than closing with zero notes on the task itself. TASK-258's own optional proposals (an explicit 'cycles' counter, tolerating the vanished file inside list_screenshot_candidates) were not implemented, but both were framed as 'not a decision'/'the smaller half' and are not required by the two generic ACs.
<!-- SECTION:FINAL_SUMMARY:END -->
