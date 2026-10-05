---
id: TASK-199
title: >-
  Open postings outside Bavaria are never re-loaded, so nothing clears their
  clinic link or retires them (AMEOS 41 linked + 188 with in_bavaria NULL)
status: To Do
assignee: []
created_date: '2026-10-01 23:18'
labels:
  - crawler
  - data
dependencies: []
priority: medium
ordinal: 196000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Agent A2's in_bavaria gate (data/geo, False only) now marks out-of-Bavaria ads False, and cli._process_rows skips in_bavaria False rows before the Matcher (and before any write). A posting that is already stored and open is therefore never re-observed: its stale clinic link is not cleared (AMEOS: 41 ads in Kiel, Stassfurt, Osnabrueck ... still linked to Bavarian clinic 18501, TASK-185 A1: 'nothing in the pipeline clears them') and it is not retired either unless the board's absence walk catches it (unverified). A2 counted 188 stored open rows (93 cities) whose in_bavaria changes NULL to False on the next intake. The tool set data/relink_task185_set.json unlinks the 41; the stored rows themselves stay open. Decision needed: retire them (reason code: none fits today, a new one such as outside_bavaria in pflege_jobs.correction_reasons would be needed) or leave them unlinked and hidden by the board filter.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The number of open postings whose city the geo gate calls non-Bavarian is measured on the live DB (with and without a stored clinic link), listed per board
- [ ] #2 Decision recorded: retire with a correction reason, or keep open and unlinked; the board's own view (app/data.py _build) is checked for what a nurse sees
- [ ] #3 Whatever is decided is applied by a change set through tools/apply_posting_changes.py with backup, read-back and corrections rows, and the nightly path no longer leaves such rows linked
<!-- AC:END -->
