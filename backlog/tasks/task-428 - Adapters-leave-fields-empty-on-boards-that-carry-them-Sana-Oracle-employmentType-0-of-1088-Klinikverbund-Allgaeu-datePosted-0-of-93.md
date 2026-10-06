---
id: TASK-428
title: >-
  Adapters leave fields empty on boards that carry them: Sana Oracle
  employmentType 0 of 1088, Klinikverbund Allgaeu datePosted 0 of 93
status: To Do
assignee: []
created_date: '2026-10-06 06:16'
labels:
  - crawler-coverage
  - adapter
dependencies: []
references:
  - backlog/tasks/task-197
priority: medium
ordinal: 298000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-197 mirror re-record on 2026-10-05. (1) The Oracle CE adapter (crawlers/vendor_adapters.py, _oracle_cx_rows) fills title, place and ad text for all 1088 Sana postings but reads no employmentType: 0 of 1088 rows carry it, so full-time and part-time cannot be told apart for Sana. (2) The umantis board of Klinikverbund Allgaeu has no datePosted on any of its 93 postings, so freshness filters (fresh_days) treat them as undated. Per board: decide whether the source page carries the field (read it) or does not (say so in the board's named gap); do not invent a value.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Sana Oracle rows carry employmentType wherever the REST detail has it, red test on the mirror first, mutation-checked; if the source has none, the board has a named gap that says so
- [ ] #2 Klinikverbund Allgaeu rows carry datePosted wherever the page shows a date, same method; otherwise a named gap
<!-- AC:END -->
