---
id: TASK-253
title: >-
  The hourly maintenance thread has no error guard, and the retention result it
  publishes carries no timestamp
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
ordinal: 200000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Found independently by 2 review lenses. Location: bridge/server.py:410. Severity: operator-blind. 

HOW IT HAPPENS: Ivan clears space by hand on the mini (exactly what the held pile invites) while the hourly pass is between glob and stat. FileNotFoundError ends maintenance_loop. From then on the ledger is never swept and no artefact is ever reviewed or deleted, while health keeps printing the last successful counts with no way to tell their age.

WHAT IT COSTS: Retention, the ledger sweep and the only publisher of 'the held pile is growing' stop together, and the surface designed to reveal it reports stale numbers that look fresh. Disk fills quietly on a machine that is not ours; the first symptom is sqlite write failures, which also take the inbound watcher down (see finding 1).

PROPOSED DIRECTION (not a decision): Wrap the pass the way every watcher wraps its own cycle: count the failure, journal it, keep the loop. Stamp last_retention with the time of the pass and how long it took so staleness is readable rather than inferred. Since maintenance is the one loop with an hour-long period, give it a health block with last_ok_at and last_error beside the watchers', not just its latest output.

VERIFICATION NOTES: maintenance_loop is literally `while not stop.wait(interval): maintenance_once(executor)` (server.py:410-412) -- one exception ends it for the process lifetime, and it is a daemon thread nothing restarts. The named trigger is real: list_screenshot_candidates/list_recording_candidates stat every path returned by rglob (adb_driver.py:1638-1645), so a file removed between glob and stat raises FileNotFoundError straight through review_and_sweep into the loop. executor.last_retention is set only on success (server.py:405) and contains only {'screenshots': {...}, 'recordings': {...}} (retention.py:167-172) -- no 'at', no duration -- and health exposes it raw (executor.py:786), so a three-week-old result is indistinguishable from a fresh one.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented. Verified the sceptic's argument against current code and agree: the finding is real and reachable (FileNotFoundError from the glob/stat TOCTOU window in adb_driver.py's list_screenshot_candidates/list_recording_candidates propagates unguarded through review_and_sweep into maintenance_once, and the bare daemon thread dies for the process's life -- reproduced by making a FakeDriver subclass raise and confirming the old code let it escape).

Fix, mirroring bridge/watcher.py's watcher-cycle idiom:
- bridge/server.py::maintenance_once now wraps the ledger sweep + retention review in try/except Exception, never re-raises. On failure: increments executor.retention_errors, sets last_retention_error/last_retention_error_at, journals "maintenance_error" via ledger.note, returns None. maintenance_loop itself is unchanged (needed no change: maintenance_once no longer raises, so its while-loop already survives).
- On success, executor.last_retention is now stamped with 'at' (L.utc(now)) and 'duration_sec' (executor.monotonic() delta), and last_retention_ok_at is set.
- bridge/executor.py: added last_retention_ok_at/retention_errors/last_retention_error/last_retention_error_at state (init'd in __init__, same shape as every watcher). health()'s "retention" key is now a heartbeat-shaped block: {last_ok_at, errors, last_error, last_error_at, result} where result is the stamped last_retention -- replaces the old raw dict that had no way to tell a fresh pass from a three-week-old one.

Tests (tests/test_bridge_executor.py):
- Updated test_maintenance_once_reviews_before_sweeping_and_surfaces_the_result_in_health for the new health()["retention"] shape (asserts last_ok_at/errors/last_error plus the stamped result).
- Added test_maintenance_once_survives_a_toctou_error_and_journals_it: a FakeDriver subclass whose list_screenshot_candidates raises FileNotFoundError; asserts maintenance_once returns None instead of raising, the error is counted/journaled/visible in health(), and a later successful pass still updates last_retention/last_ok_at normally. Verified this test (and the updated one) FAIL against the pre-fix code (reverted server.py locally, confirmed FileNotFoundError propagates out of maintenance_once and the health-shape assertions fail), then confirmed both pass with the fix restored.

Ran narrow tests only, as instructed: tests/test_bridge_executor.py (150 passed) and tests/test_bridge_retention.py (23 passed, untouched but exercises the same review_and_sweep path). Did not run the full suite.

Grepped app/ and tools/ for any reader of health()["retention"]'s old raw shape -- none found, so this is not a breaking change for any consumer in this repo.

Left at In Progress per instructions; did not check acceptance criteria or mark Done.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
bridge/server.py:492-528 (maintenance_once try/except + last_retention stamping) and bridge/executor.py:972-976 (health()['retention'] heartbeat shape) match the claim exactly; tests/test_bridge_executor.py:1447/1467 cover it and the full 179-test file passes.
<!-- SECTION:FINAL_SUMMARY:END -->
