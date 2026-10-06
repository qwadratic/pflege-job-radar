---
id: TASK-275
title: >-
  Screen recordings orphaned on the handset are never pulled, never deleted, and
  nothing watches free space
status: Done
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 222000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/adb_driver.py:1666. Severity: degraded. 

HOW IT HAPPENS: The bridge unit restarts (deploy, OOM, reboot) while an op is recording. screenrecord runs to its 180 s limit and leaves a 720p mp4 on /sdcard that nothing lists, sweeps or pulls. Repeat per restart.

WHAT IT COSTS: Slow, silent storage growth on the one device the whole rail depends on. A full /sdcard breaks precisely what this rail needs most: WhatsApp stops saving incoming media, screencap fails, and the uiautomator dump path that every read depends on fails with driver errors that look like a busy phone.

PROPOSED DIRECTION (not a decision): Make the phone-side artefact self-limiting instead of dependent on a matching stop call: a start-up pass that removes /sdcard/op.*.mp4 left by a previous process costs nothing and needs no bookkeeping, and the ops path's stop should send the `killall -2 screenrecord` even when the map is empty. Separately, a free-space check (TASK-361's, never built) now has more reason to exist with capture default-on and retention holding indefinitely -- and it has to cover the handset, not just the mini.

VERIFICATION NOTES: start_recording spawns screenrecord to /sdcard/{op_id}.mp4 and records the op only in the in-memory self._recordings dict (adb_driver.py:1666-1673); stop_recording is the only code that pulls and `rm -f`s it, and it returns immediately when the dict has no entry (1675-1677). A process restart empties the dict, so the mp4 stays on /sdcard: MediaWatcher walks only WA_MEDIA_ROOT = /sdcard/WhatsApp/Media (media.py:75), retention.py sweeps mini-side directories only, and no code anywhere checks handset or mini free space (greps for df/statvfs/free space in bridge/ and tools/ come back empty). Debug capture is on by default (server.py main), and Restart=always makes mid-op restarts ordinary.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified the sceptic's finding against current code and confirmed it: debug_capture defaults on (server.py main(), WA_BRIDGE_DEBUG_CAPTURE default "1"), dispatcher.run_one() starts a recording per op and stops it only in a finally in the same process (dispatcher.py), and start_recording's only bookkeeping is the in-memory AdbDriver._recordings dict -- a restart between start and stop orphans the phone-side op.<id>.mp4 with nothing left to name it. Direct parallel to ledger.py::_recover_stuck_ops, which already treats "restart lands mid-op" as real and self-heals the matching ledger row on every Ledger.__init__.

Fix implemented, minimal blast radius as scoped:
- bridge/adb_driver.py: new AdbDriver.sweep_orphaned_recordings() next to start_recording/stop_recording (TASK-228 section). Sends `rm -f /sdcard/op.*.mp4` -- matches retention.py's own RECORDING_OP_RE / mint_op_id() naming ("op." + 24 hex). Not added to the PhoneDriver ABC or FakeDriver: nothing calls this generically through self.driver the way describe()/list_recording_candidates() are (those are called from executor.py/retention.py with FakeDriver substituted in tests) -- this is only ever called once, directly on the concrete AdbDriver, from server.py::main(), so adding abstract surface for it would be an unused addition.
- bridge/server.py::main(): one call, `driver.sweep_orphaned_recordings()`, right after `driver = AD.AdbDriver(...)`, before the ops dispatcher (or anything else) starts -- so no recording can possibly be in flight yet when it runs. Mirrors `ledger = L.Ledger(...)` self-healing stuck rows in its own __init__ on the line above.
- tests/test_bridge_adb.py: test_sweep_orphaned_recordings_removes_whatever_op_mp4s_are_still_on_the_device, using the existing ScriptedAdb/build_capture fixture. Confirmed it fails on main() (AttributeError: no such method) and passes with the fix.

Deliberately left out (per the sceptic's own reasoning, checked and agreed): the task's secondary suggestion to also send `killall -2 screenrecord` unconditionally at startup. screenrecord's own --time-limit 180 finalizes the mp4 container without SIGINT, and the sweep here deletes the file outright rather than reading/playing it, so container validity is moot for this fix; skipping it only costs up to 180s of timing, not correctness. Also left the PhoneDriver/FakeDriver surface untouched (see above) and left handset free-space alarming out of scope (TASK-361's).

Ran only the narrow test file: .venv/bin/python -m pytest tests/test_bridge_adb.py -q -> 92 passed. Not committed; not touching acceptance criteria -- leaving both for the verification pass.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fix and regression test both verified present and correct in the current working tree; the secondary free-space-alarm suggestion in the task's own PROPOSED DIRECTION is explicitly and reasonably scoped out with a written argument, which AC#1 allows.
<!-- SECTION:FINAL_SUMMARY:END -->
