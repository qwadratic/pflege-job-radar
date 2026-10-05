---
id: TASK-195
title: >-
  verify.py judges P&I rows by title; two vacancies with one title cannot be
  told apart
status: To Do
assignee: []
created_date: '2026-10-01 21:01'
labels:
  - verify
  - pi_asp
dependencies: []
ordinal: 192000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-184 A3 note: after the P&I rows got a stable ref (#position,id=<uuid>) the verify stage still judges P&I postings by title (docstring: no per-posting page). Two vacancies sharing a title (Coburg ICU, 2 vacancies) cannot be told apart when one is closed. A position-id check against the board listing is possible (the board lists position ids). Decide: verify by position id on the board's own listing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A P&I posting is live iff its position id is in the board's current listing (not by title)
- [ ] #2 Two vacancies with one title: closing one retires only that one (test from real data: Coburg ICU pair)
<!-- AC:END -->
