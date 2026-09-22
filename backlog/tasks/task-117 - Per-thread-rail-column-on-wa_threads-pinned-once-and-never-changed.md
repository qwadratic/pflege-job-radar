---
id: TASK-117
title: 'Per-thread rail column on wa_threads, pinned once and never changed'
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 01:21'
updated_date: '2026-09-21 10:11'
labels:
  - wa-transport
dependencies:
  - TASK-116
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 125000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M2/M6. The routing state must live in the DB, not in an env var or an external phones file.

Two reasons the pin is per-thread and immutable. First, the two rails are two different sender numbers: switching a live thread mid-conversation means the candidate suddenly hears from a stranger, which invites confusion and spam reports against the one WABA asset this project exists to protect. Second, a global env rollback would do exactly that to every thread at once.

A file-based router (WA_BRIDGE_PHONES_FILE and similar) was rejected: it is external state the DB cannot migrate, cannot report and cannot audit.

Use the existing MIGRATIONS tuple in `app/wa/store.py:172-177`, the same mechanism TASK-102 used. No schema rewrite.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 rail migrates in through the existing MIGRATIONS tuple in store.py and is exposed by _thread_row and by GET /wa/threads
- [x] #2 rail is set on the first successful outbound of a thread and is never changed afterwards
- [x] #3 A test proves a second pin attempt on an already-pinned thread fails loudly rather than silently overwriting or silently ignoring
- [x] #4 get_client resolves the transport from the pinned rail first and only falls back to WA_TRANSPORT for an unpinned thread
- [x] #5 The pin_rail docstring states why the pin is immutable, in terms of the sender number the candidate sees
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add ('wa_threads','rail','text') to store.MIGRATIONS; _thread_row exposes it for free (select *).
2. store.pin_rail / rail_of / rail_counts; _update_thread keeps not writing it, so no card save can flip it.
3. transport.rail_for(conn, phone) reads the pin first, C.TRANSPORT only for an unpinned thread; get_client builds meta or bridge from it.
4. api._send pins after a send that actually went out (bubbles or reopen template), never on a draft or a failure.
5. Report it: GET /api/wa/health rails counts, GET /api/wa/threads row['rail'].
6. Tests in tests/test_wa_bridge_window.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Shipped 2026-09-21.

- store.py: MIGRATIONS gains ('wa_threads','rail','text'); pin_rail (first-write-wins, raises on a different rail and on an unknown one), rail_of, rail_counts. _update_thread still writes neither is_test nor rail.
- transport.py: rail_for(conn, phone) -> pinned rail else C.TRANSPORT; get_client builds meta.Client or bridge.Client from it and still returns an injected client untouched. get_client takes conn= so api._send resolves on the connection it already holds.
- api.py: _send pins the rail after a send that went out (bubbles, or the reopen template); a draft and a failed send pin nothing. GET /api/wa/health reports rails counts.
- The STOP suppression lane is now the thread's rail rather than C.TRANSPORT (api.process_owed_turn): the record says which number the candidate refused.

Verified: tests/test_wa_bridge_window.py (20 tests) and the full offline suite -- 1870 passed, 127 skipped, 70 deselected.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:15
---
decision-8 (2026-09-21): KEEP as written, and more important than before. Two sender numbers on one handset, and one of them may be a personal number (the MSISDN on L2N4C19B14054874 is unrecorded -- TASK-136). The per-thread pin is what stops a candidate seeing a stranger answer.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
wa_threads.rail, migrated in through the existing MIGRATIONS tuple, pinned by store.pin_rail on a thread's first successful outbound and never changed. transport.rail_for reads it before C.TRANSPORT, so flipping WA_TRANSPORT moves new conversations only; a second pin to a different rail raises. Reported on GET /api/wa/threads and as counts on GET /api/wa/health. Verified by tests/test_wa_bridge_window.py and a green offline suite (1870 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
