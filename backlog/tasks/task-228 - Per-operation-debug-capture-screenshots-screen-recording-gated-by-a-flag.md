---
id: TASK-228
title: 'Per-operation debug capture: screenshots + screen recording, gated by a flag'
status: To Do
assignee: []
created_date: '2026-09-23 03:11'
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
- [ ] #1 With the flag on, every dispatched op leaves pre/post screenshots and a playable, downscaled mp4 under its own op_id in the filename
- [ ] #2 With the flag off (default), capture adds no overhead and produces no files
- [ ] #3 Screenshots and recordings are both swept at 14 days retention
- [ ] #4 A capture failure (screenshot or screenrecord) never fails the underlying phone operation
<!-- AC:END -->
