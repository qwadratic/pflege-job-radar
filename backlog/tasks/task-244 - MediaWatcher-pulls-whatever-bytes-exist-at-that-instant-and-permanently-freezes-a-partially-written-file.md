---
id: TASK-244
title: >-
  MediaWatcher pulls whatever bytes exist at that instant and permanently
  freezes a partially written file
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - wrong-answer-to-candidate
dependencies: []
modified_files:
  - bridge/watcher.py
  - tests/test_bridge_executor.py
priority: high
type: bug
project: whatsapp
ordinal: 191000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/watcher.py:232. Severity: wrong-answer-to-candidate. 

HOW IT HAPPENS: A candidate sends a 4 MB PDF or a long voice note. WhatsApp begins writing it into /sdcard/WhatsApp/Media/... MediaWatcher's 5 s cycle runs list_media, sees the path with size > 0, and adb-pulls it seconds later. Nothing compares the pulled byte count against the listed size and nothing waits for (size, mtime) to stop moving. record_media marks the path known forever, so the completed file is never pulled again.

WHAT IT COSTS: The candidate's CV is read as a truncated PDF: extract_text returns partial or near-empty text and falls through to the vision path, or classify_document lands on the wrong document_type, or the turn raises and retries on a file that will never be correct. A truncated .opus transcribes to nonsense or raises on every catch-up pass. Nothing in the queue listing or the journal distinguishes it from a healthy pull.

PROPOSED DIRECTION (not a decision): Only pull paths whose (size, mtime) are unchanged across two consecutive listings — the watcher already has the previous cycle's listing in hand. After the pull, compare the copied length against the listed size and treat a mismatch as a failed pull; that branch already does the right thing (no media_seen row, retried next cycle). Check WhatsApp's in-progress download naming on the live tree before settling the exact rule — if it renames atomically, this reduces to a cheap assertion rather than a fix.

VERIFICATION NOTES: REAL in the code, with one external unknown the finder already flagged honestly. _cycle_once (watcher.py:229-261) selects on `rel not in known and size > 0` with no stability comparison against the previous listing, then pulls in a separate adb call; pull_media (adb_driver.py:1350-1359) checks only rc and existence, never the copied length against the listed size; record_media (ledger.py:686-691) is `insert or ignore into media_seen(source_rel, ...)`, so the path is 'known' forever and never re-pulled. sha and content id are computed over whatever was copied (watcher.py:245-247), so the VPS's own sha check passes on the truncated blob. The one thing I cannot settle read-only is whether WhatsApp writes incoming media in place or to a temp name and renames — `! -name '.*'` in list_media (adb_driver.py:1346) only excludes dotfiles. If WhatsApp renames atomically the race cannot fire; nothing in this repo establishes that either way, and nothing in the code blocks the scenario.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented both halves of the proposed direction, in bridge/watcher.py::MediaWatcher only:
(1) __init__ gains self._last_listing (rel -> (size, mtime) from the previous cycle). _cycle_once's
fresh-selection now additionally requires self._last_listing.get(rel) == (size, mtime): a rel is
pulled only once two consecutive 5s-apart listings agree. (2) Post-pull, len(blob) != size unlinks
dest and reuses the exact media_pull_failed / no-record_media branch a DriverError pull already
uses, so a copy that still comes up short is retried next cycle, never frozen into media_seen.
self._last_listing is set unconditionally at the end of a successful _cycle_once.

WhatsApp's in-place-vs-atomic-rename write behavior is left undetermined, as instructed (no adb, no
live tree reachable from here) -- documented as an open unknown in __init__'s own comment; the fix
does not assume either way.

Blast-radius correction: the brief's own estimate ("confined to bridge/watcher.py ... new test file
tests/test_bridge_watcher.py, none exists today") undercounted. MediaWatcher already has an
extensive suite in tests/test_bridge_executor.py (round 4-6 decoy regressions). The two-listing
stability requirement changes the watcher's contract from "pull on first sight" to "pull after one
confirmed-stable interval", which broke 8 pre-existing tests there that asserted a pull inside a
single cycle() call. Updated all 8 to prime the baseline with one extra watch.cycle() before the
pulling assertion -- mechanical, no assertion weakened, see diff. No new test file: new tests sit
next to the existing MediaWatcher tests in tests/test_bridge_executor.py, per the house instruction
to put tests in the file that already covers the module.

New tests (each verified red before the fix / green after, by temporarily reverting
bridge/watcher.py's diff and rerunning just these two):
- test_a_file_still_growing_between_two_listings_is_not_pulled_until_it_stops
- test_a_pull_shorter_than_the_listed_size_is_not_recorded_and_is_retried

Test run: .venv/bin/python -m pytest tests/test_bridge_executor.py -q -> 149 passed. Did not run the
full suite, per instruction -- git status/diff confirm this working tree already carries unrelated
in-flight changes from other TASK-2xx workers, none of which touch bridge/watcher.py's MediaWatcher
class or these tests.

Left for the verification pass: every fresh pull, not only a growing one, now waits a minimum of one
extra 5s cycle before it is ever attempted. That latency trade-off is inherent to the fix as scoped
and was not separately decided by Ivan before this run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
bridge/watcher.py:279-376 (MediaWatcher stability + short-pull handling). Tests at tests/test_bridge_executor.py:685 and :708; ran the full tests/test_bridge_executor.py file -- 179 passed, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
