---
id: TASK-266
title: >-
  The idle self-check parks the phone out from under a human using it, with no
  check that anyone is at the handset
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 12:58'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 213000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/watcher.py:136. Severity: degraded. 

HOW IT HAPPENS: An operator picks up the handset to read a thread or to type something by hand. Two cycles (~10 s) of a Conversation being in focus, a successful non-blocking lock probe, and the watcher parks the phone back to the launcher underneath them.

WHAT IT COSTS: Manual work on the handset is effectively impossible past ten seconds, which matters because manual work is currently the only way to inspect a thread, attach a file a human has to place, or verify anything by eye — and, per the finding above, the only way anyone discovers a message the shade never captured.

PROPOSED DIRECTION (not a decision): Test the missing half of the premise before parking: the handset can report screen state and time since last user interaction, either of which distinguishes a forgotten chat from a person holding the phone. A short operator hold (a flag file or a ledger row that suppresses the idle check for N minutes) is cheaper to reason about and would do.

VERIFICATION NOTES: CONFIRMED with one detail corrected. _check_idle_dirty (watcher.py:109-138) tests only `focus().endswith('Conversation')`; two consecutive cycles at DEFAULT_INTERVAL_SEC=5 s, a non-blocking lock probe (timeout=0) that succeeds because a human at the handset holds no flock, and park() fires — BACK out of the chat, then HOME (adb_driver.py:1616-1629). There is no screen-state, keyguard or last-user-interaction check on the path; I checked AdbDriver for one and it has only wake(). The docstring's premise 'a chat left open with nothing queued and nobody at the phone' really does test only the first half. CORRECTION: 'a half-typed draft is discarded by the BACK press' is not established — WhatsApp preserves drafts on back-out and nothing in this repo says otherwise; drop that clause. The defect is that manual use of the handset is bounded at ~10 seconds.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed. bridge/watcher.py::InboundWatcher._check_idle_dirty gained a third, read-only check before
the lock probe: an operator hold. InboundWatcher now takes operator_hold_path (wired in
bridge/server.py's main() to WA_BRIDGE_STATE/operator_hold); a new _operator_hold_until(now) reads
that file (one RFC3339 timestamp, ledger.utc's own spelling, written by hand -- same by-hand,
no-tool convention docs/whatsapp.md already uses for WA_AGENT_PAUSED.flag) and, while now is before
the stated expiry, the confirmed-dirty streak logs idle_dirty_held (a new counter, also in
heartbeat()) and returns instead of taking the lock/parking. Missing file = no hold (unchanged
behaviour). Unreadable/malformed file is logged (idle_dirty_hold_unreadable) and treated as no
hold -- fails open to the pre-existing park, never blocks the TASK-225 recovery silently forever.

Did NOT add dumpsys wakefulness as a signal (sceptic's optional part (b)): screen-on doesn't
distinguish "someone is reading" from "the OS hasn't hit its configured timeout yet" with no record
of this handset's screen-timeout setting -- said in the new method's own docstring rather than wired
in as an unreliable auto-detect. Did NOT add an HTTP route or a tools/wa_bridge.py subcommand to set
the hold: nothing in this repo currently exercises the file-by-hand convention through code either
(WA_AGENT_PAUSED.flag is "in place" / "by hand only" per docs/whatsapp.md), and adding a new
authenticated HTTP surface + CLI subcommand was judged out of the finding's own blast radius -- an
operator sets the file directly on the mini. Did not touch take_phone/_recover_if_dirty
(executor.py) -- out of scope per the task, a separate opportunistic check on a different trigger.

Test: tests/test_bridge_executor.py::test_an_operator_hold_stops_the_idle_check_from_parking_a_human_at_the_handset
(TASK-226 idle-check section). Verified it fails on the pre-fix code
(TypeError: unexpected keyword argument 'operator_hold_path') and passes with the fix.
tests/test_bridge_executor.py -q: 157 passed, 1 deselected (test_maintenance_once_reviews_before_sweeping_and_surfaces_the_result_in_health
fails on a FileNotFoundError under /shots/... unrelated to this change -- confirmed failing
identically with this diff stashed out, pre-existing, not touched).
<!-- SECTION:NOTES:END -->
