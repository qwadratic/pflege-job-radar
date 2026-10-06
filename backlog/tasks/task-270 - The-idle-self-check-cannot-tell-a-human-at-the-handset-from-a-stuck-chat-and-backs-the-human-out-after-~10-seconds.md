---
id: TASK-270
title: >-
  The idle self-check cannot tell a human at the handset from a stuck chat, and
  backs the human out after ~10 seconds
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
ordinal: 217000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/watcher.py:130. Severity: degraded. 

HOW IT HAPPENS: An operator picks up the handset and opens a chat to read it -- literally the 'open the phone and look at that conversation with Valy' workflow. About ten seconds later the watcher presses BACK and then HOME under their fingers. Same for an operator composing a manual reply in the composer.

WHAT IT COSTS: The handset is unusable by a human for anything longer than two watcher cycles, with no override -- and looking at the phone is exactly how an operator resolves a TASK-225-class incident. Every occurrence is journalled as idle_dirty_recovered, so routine human use inflates the very counter meant to mean 'something upstream got stuck'.

PROPOSED DIRECTION (not a decision): Give a human a way to say 'I am holding the phone' that this loop respects. The simplest is the same flock the loop already probes, taken by an operator command (tools/wa_bridge.py) as a one-shot hold with a timeout -- a manual hold then looks identical to a legitimate op, which is the correct semantics, and it costs no new state. Requiring the screen to be unchanged between cycles (a human reading scrolls; a stuck chat does not) is a reasonable second layer, but the explicit hold is the part worth doing first. While in there, either implement the 'nothing queued' condition the docstring claims (ledger has the queue depth) or drop the claim.

VERIFICATION NOTES: CONFIRMED as written, with one sequencing caveat. IDLE_DIRTY_CONFIRM_CYCLES = 2 (watcher.py:46) against DEFAULT_INTERVAL_SEC = 5.0, so two consecutive cycles reading a focus ending in 'Conversation' -- about 10 s -- trips it, and the only guard before BACK+HOME is the non-blocking flock probe at watcher.py:130 (`lock(timeout=0)`), which nothing a human holding the phone ever takes. park() (adb_driver.py:1616-1630) presses KEYCODE_BACK then KEYCODE_HOME. Re-opening the chat just restarts the 10 s timer, and each fire increments idle_dirty_recovered and journals it. The method's docstring claims it acts only 'with nothing queued', but there is no queue check in the code at all -- the flock probe is the entire guard. CAVEAT, worth saying plainly: on the current build this cannot fire, because finding 1 kills the watcher thread on its first cycle at the very focus() call this check begins with. It becomes live the moment AdbDriver.focus() is implemented, so it must be fixed in the same change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Duplicate of TASK-266; same defect, same code path. Closed in favour of TASK-266, which carries the detail.
<!-- SECTION:FINAL_SUMMARY:END -->
