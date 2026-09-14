---
id: TASK-92
title: Quiet hours for proactive follow-up nudges
status: Done
assignee: []
created_date: '2026-09-13 20:22'
updated_date: '2026-09-13 20:26'
labels: []
dependencies: []
type: feature
ordinal: 92000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan confirmed after the TASK-91 comparison report: the real reference implementation's proactive nudges never fire during a candidate's likely sleep window; app/wa/luna/followups.py (TASK-85) currently has no such guard and could send an unprompted nudge at 3am. Dedup fingerprinting (the source's other quiet-hours-adjacent feature) is explicitly uncertain/not yet requested -- out of scope for this task, to be raised separately.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 followups.py skips its entire sweep (returns [] from run(), no thread checked) when the current time falls inside a configured local quiet-hours window
- [x] #2 the window is configurable via env vars with sensible defaults (single fixed timezone, since no per-candidate timezone data exists on this board) and a wrap-past-midnight window (e.g. 21:00-09:00) works correctly
- [x] #3 a nudge due during quiet hours is not lost -- the next 15-min timer tick re-evaluates and sends once outside the window (no missed sends, no separate deferred-send queue)
- [x] #4 catch-up (app/wa/luna/catchup.py, TASK-78) is unaffected -- quiet hours only gates unprompted proactive nudges, never a reply owed to something the candidate already said
- [x] #5 offline tests cover the boundary (just inside/outside the window, including the midnight wrap) and pass; pflege-wa-followups.timer keeps running unchanged (no service/timer file changes needed)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented in app/wa/config.py (QUIET_HOURS_START/END/TZ, defaults 21/9/Europe-Berlin, matching the board's Bavaria-only scope) and app/wa/luna/followups.py (_in_quiet_hours(now=None) using zoneinfo, wraps past midnight when START>END, START==END reads as disabled/fail-open rather than 'quiet all day' so a config typo can't silently and permanently kill every nudge; run() returns [] immediately without touching any thread when quiet; main() prints a distinct '0 nudge(s) sent (quiet hours: ...)' line instead of a bare zero so an operator reading journalctl can tell why). No deferred-send queue: _eligible_tier is driven purely by elapsed time since last_outbound_at, so a tier due during quiet hours is simply re-detected and sent on the next 15-min tick once outside the window -- verified by test_a_nudge_due_during_quiet_hours_is_not_lost_the_next_tick_sends_it. catchup.py untouched (quiet hours only gates followups.py's proactive sweep). The existing test fixture (tests/test_wa_luna_followups.py:db) now disables quiet hours (START=END=0) by default so the 11 pre-existing tier/streak tests never flake depending on real wall-clock time; 5 new tests cover a simple window, the midnight-wrap default, the disabled zero-width case, and the run()-level skip-then-catch-up-next-tick behavior. Offline suite: 1164 passed (up from 1159), same pre-existing unrelated network-collection skip in test_completeness_dvinci.py. pflege-wa-followups.service is Type=oneshot spawned fresh by its timer every 15 min -- no process to restart, the next tick already runs the new code from disk. Dedup fingerprinting (the source's other quiet-hours-adjacent feature) was explicitly left out per Ivan's own 'not sure if' -- not implemented, not scoped into this task; flagged back to him separately (this harness's single-timer/single-lock design may not even need it, unlike the source's multiple overlapping wake triggers).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ported the real reference implementation's quiet-hours guard for proactive follow-up nudges: app/wa/luna/followups.py now skips its entire 15-minute sweep during a configured local time window (default 21:00-09:00 Europe/Berlin) rather than potentially nudging a candidate at 3am. Nothing is lost -- a nudge due during the window fires on the next tick once outside it, since eligibility is computed from elapsed time, not from when a check happened to run. catchup.py (replies owed to something the candidate already said) is deliberately untouched. Verified with 5 new tests (simple window, midnight wrap, disabled zero-width config, and the skip-then-catch-up-next-tick behavior) plus the existing 11 followups tests fixed to no longer depend on real wall-clock time; full offline suite green at 1164 passed. No service restart needed (oneshot timer job).
<!-- SECTION:FINAL_SUMMARY:END -->
