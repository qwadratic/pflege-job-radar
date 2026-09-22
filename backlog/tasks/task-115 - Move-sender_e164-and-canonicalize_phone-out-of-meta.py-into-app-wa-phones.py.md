---
id: TASK-115
title: Move sender_e164 and canonicalize_phone out of meta.py into app/wa/phones.py
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 01:20'
updated_date: '2026-09-21 02:50'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: chore
ordinal: 123000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M2, first step. Ship alone as a no-behaviour-change commit.

`sender_e164` (`app/wa/meta.py:50`) and `canonicalize_phone` (`meta.py:56`) are pure phone-number helpers with nothing to do with the Meta transport, yet six non-transport call sites already import them from there: `app/wa/api.py:149` and `:418`, `app/wa/luna/campaign.py:239`, `app/wa/luna/import_history.py:140`, `app/wa/luna/migrate_candidates.py:46`, `app/wa/luna/export_known_phones.py:42`, `app/wa/luna/test_threads.py:35`. Three of those modules -- migrate_candidates.py, export_known_phones.py, test_threads.py -- import meta.py for nothing else at all. Once a second transport exists, every one of those imports drags the Meta client in for no reason.

Smallest possible first move, so the riskier seam work that follows lands on a clean base.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/wa/phones.py holds sender_e164 and canonicalize_phone verbatim, with no behaviour change
- [x] #2 meta.py re-imports both names so every existing import path keeps working and no call site is edited in this task
- [x] #3 The full offline suite passes with zero edits to any existing test file
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Move sender_e164 and canonicalize_phone verbatim into a new app/wa/phones.py.
2. Re-export both from meta.py (`from .phones import ...  # noqa: F401`) so no call site is edited.
3. Cover both helpers in tests/test_wa_transport.py against the shapes the existing callers rely on, including the truthy-but-bogus "+49" that every MIN_PHONE_DIGITS check depends on.
4. Run the full offline suite with zero edits to any existing test file.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented in the working tree (uncommitted).

Files: app/wa/phones.py (new, both helpers verbatim), app/wa/meta.py (helpers deleted, re-exported at line 18).

Call sites untouched by design (AC#2): all six still reach the helpers through `M.`. migrate_candidates.py, export_known_phones.py and test_threads.py therefore still import the Cloud API client only to normalise a number -- stated in the app/wa/phones.py docstring so the next reader does not assume the move already freed them.

Verified: PFLEGE_TESTS_OFFLINE=1 .venv/bin/python -m pytest -q -m "not network and not llm" -> 1682 passed, 127 skipped, 70 deselected (149s). `git status --short tests/` shows one untracked file (tests/test_wa_transport.py) and no modified test file, so AC#3 "zero edits to any existing test file" holds literally.

AC#1/#2 evidence: tests/test_wa_transport.py::test_meta_still_exports_the_helpers_it_moved_out asserts `M.sender_e164 is P.sender_e164` and the same for canonicalize_phone (identity, not equality -- a re-export, not a copy); the parametrised sender_e164/canonicalize_phone cases cover the four shapes one human has plus the truthy-but-bogus "+49" that every MIN_PHONE_DIGITS caller depends on. `git diff app/wa/meta.py` is the two function bodies removed and one re-export line added, nothing else.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Moved sender_e164 and canonicalize_phone verbatim from app/wa/meta.py into the new app/wa/phones.py and re-exported both from meta.py, so a second transport need not import the Cloud API client to normalise a number and no call site changed. Verified by tests/test_wa_transport.py (identity of the re-exports plus the behaviour cases the MIN_PHONE_DIGITS callers rely on) and by the full offline suite: 1682 passed, 127 skipped, with no existing test file edited.
<!-- SECTION:FINAL_SUMMARY:END -->
