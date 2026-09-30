---
id: TASK-226
title: >-
  Pre-flight dirty-state check + periodic idle self-check (parks the phone
  without needing another operation)
status: Done
assignee: []
created_date: '2026-09-23 03:11'
updated_date: '2026-09-23 03:17'
labels: []
dependencies: []
project: whatsapp
ordinal: 173000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-225 fix, part A. bridge/executor.py::take_phone should check driver.focus() right after acquiring the lock and park()+log if dirty ('dirty_state_recovered'). bridge/watcher.py::InboundWatcher's existing 5s cycle should add the same focus() check when the phone lock is not held, park()+log if dirty ('idle_dirty_recovered') -- this is the piece that catches drift with zero operations happening, which is what actually happened in TASK-225's incident. Both counters surface in /v1/health next to watcher.cycles/errors.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 take_phone() recovers a dirty phone before returning the lock to any caller, and logs dirty_state_recovered with the focus value found
- [x] #2 InboundWatcher's 5s cycle independently detects and recovers a dirty phone even when no operation is queued or running, logging idle_dirty_recovered
- [x] #3 /v1/health surfaces both counters so a quiet rail and a blind rail are distinguishable without manual dumpsys/uiautomator forensics
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented: bridge/executor.py::take_phone calls _recover_if_dirty() right after acquiring the lock (checks driver.focus().endswith('Conversation'), parks + logs dirty_state_recovered if so, counter on Executor.dirty_recovered, surfaced in /v1/health under inbound.dirty_recovered). bridge/watcher.py::InboundWatcher._check_idle_dirty() runs every 5s cycle independent of any operation: 2 consecutive dirty reads (IDLE_DIRTY_CONFIRM_CYCLES) trigger a non-blocking lock probe (timeout=0) before ever touching the UI, so a legitimate in-flight send is never raced -- only skipped-and-retried next cycle. Counter idle_dirty_recovered surfaced in watcher.heartbeat(). Added focus()/focus_value to PhoneDriver/FakeDriver (bridge/driver.py) for the abstraction and test support.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Two independent recovery paths for a chat left open (TASK-225's exact failure mode): take_phone()'s pre-flight check for anything still going through the mediated executor path, and InboundWatcher's own 5s-cycle idle self-check for drift from any source (including non-mediated/manual touches), which is what actually would have caught tonight's incident with zero operations happening. Both counters (dirty_recovered, idle_dirty_recovered) surface in /v1/health. Verified: 5 new tests (tests/test_bridge_executor.py) covering dirty-recovery, clean-phone-no-op, single-cycle-not-enough, sustained-dirty-parks, and never-races-a-held-lock; full bridge/wa_bridge offline lane (502 tests) passes with no regressions.
<!-- SECTION:FINAL_SUMMARY:END -->
