---
id: TASK-228
title: 'Per-operation debug capture: screenshots + screen recording, gated by a flag'
status: Done
assignee: []
created_date: '2026-09-23 03:11'
updated_date: '2026-09-23 04:01'
labels: []
dependencies: []
project: whatsapp
ordinal: 175000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-225 fix, part C, depends on the ops-queue task's op_id. Gated behind WA_BRIDGE_DEBUG_CAPTURE=1 (off by default). Dispatcher takes a screenshot before/after every job (and on error), reusing driver.screenshot() (bridge/adb_driver.py:549-555), tagged {op_id}_00_pre.png / _01_post.png / _02_error.png in the existing shots_dir. Also starts adb shell screenrecord --size <reduced> --time-limit 180 /sdcard/{op_id}.mp4 at job start, stops it with a clean signal (SIGINT, not SIGKILL -- SIGKILL leaves the mp4 unfinalized) at job end, adb pulls it to a new recordings/ dir and removes the on-device copy. The 180s Android ceiling is a real OS limit -- document it plainly, a job that runs longer gets a truncated recording, not an error. Screenshots get a resize pass via ImageMagick convert (confirmed present on the mini, no Pillow installed) before being written. Retention for both screenshots and recordings: 14 days (Ivan, 2026-09-23) -- raise SCREENSHOT_RETENTION_DAYS (bridge/driver.py:42) from 7 to 14, and sweep recordings/ on the same constant. Capture failures must never fail the underlying operation (log-only, same pattern as the existing park_failed note).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 With the flag on, every dispatched op leaves pre/post screenshots and a playable, downscaled mp4 under its own op_id in the filename
- [x] #2 With the flag off (default), capture adds no overhead and produces no files
- [x] #3 Screenshots and recordings are both swept at 14 days retention
- [x] #4 A capture failure (screenshot or screenrecord) never fails the underlying phone operation
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented: bridge/driver.py PhoneDriver gains debug_shot(op_id, tag)/start_recording(op_id)/stop_recording(op_id)/sweep_recordings(now, days=) abstract verbs + SCREENSHOT_RETENTION_DAYS 7->14 (now shared by both screenshot and recording sweeps). bridge/adb_driver.py: Adb gains spawn_shell(cmd) (background Popen for screenrecord, overridable in tests) and downscale(path) (ImageMagick convert, best-effort/non-raising); AdbDriver.debug_shot() screenshots+downscales to {shots_dir}/{op_id}_{tag}.png; start_recording() spawns 'screenrecord --size 720x1280 --time-limit 180'; stop_recording() sends 'killall -2 screenrecord' (SIGINT, not SIGKILL, so the mp4 finalizes), pulls to {recordings_dir}/{op_id}.mp4, removes the on-device copy -- returns None on a failed pull; sweep_recordings mirrors sweep_screenshots' mtime sweep. bridge/dispatcher.py::OpsDispatcher takes a debug_capture=False constructor flag; when on, run_one() takes a 00_pre shot + starts recording before the dispatched method, a 01_post shot on success or 02_error shot on refusal/exception, and always stops the recording in a finally -- every capture step goes through _capture(), which logs (ledger note debug_capture_failed) and swallows any exception rather than failing the op, same shape as the pre-existing park_failed note. bridge/server.py::main() reads WA_BRIDGE_DEBUG_CAPTURE (1/true/yes) into that flag, off by default; wires recordings_dir=WA_BRIDGE_STATE/recordings into AdbDriver; maintenance_once() now also sweeps recordings and reports the count. FakeDriver (bridge/driver.py) and ScriptedAdb (tests/test_bridge_adb.py) both grew matching scriptable stand-ins. Verified offline only (ScriptedAdb/FakeDriver, same boundary this whole driver is tested at per its own module docstring) -- no live-handset run against a real screenrecord/convert this session.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Per-op debug capture (pre/post/error screenshots + a bracketing screen recording, filenames keyed on op_id) landed in the FIFO dispatcher (TASK-227), gated off by WA_BRIDGE_DEBUG_CAPTURE (default unset). Screenshots downscale via ImageMagick convert; recordings stop with SIGINT (killall -2) so the mp4 finalizes, then get pulled and the on-device copy removed. Retention for both raised to 14 days on the one shared SCREENSHOT_RETENTION_DAYS constant. A capture failure is logged (debug_capture_failed journal note) and never fails the underlying op. Verified: 6 new tests in tests/test_bridge_adb.py (filename/downscale, screenrecord command shape, SIGINT+pull+cleanup, no-op stop, failed-pull returns None, retention sweep) + 4 new tests in tests/test_bridge_executor.py (off touches nothing, on brackets pre/post, on-refusal takes an error shot not a post shot, capture failure is logged and non-blocking); full bridge lane (tests/test_bridge_executor.py + test_bridge_adb.py + test_bridge_operations.py) at 255 passed, 0 regressions.
<!-- SECTION:FINAL_SUMMARY:END -->
