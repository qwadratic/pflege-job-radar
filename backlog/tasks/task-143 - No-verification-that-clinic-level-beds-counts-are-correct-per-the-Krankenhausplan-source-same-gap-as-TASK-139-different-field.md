---
id: TASK-143
title: >-
  No verification that clinic-level 'beds' counts are correct per the
  Krankenhausplan source (same gap as TASK-139, different field)
status: To Do
assignee: []
created_date: '2026-09-23 16:34'
updated_date: '2026-09-25 00:10'
labels:
  - db-quality
dependencies: []
priority: low
type: task
ordinal: 143000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same shape as TASK-139 but for the beds field instead of fachrichtungen: nothing checks that a clinic's registry beds count is actually right against the source Krankenhausplan PDF entry, only that the field isn't obviously garbled (TASK-131/133). beds also feeds TASK-140's live-postings/beds ratio directly -- a wrong beds number would silently distort that metric too, so this is worth doing alongside or before trusting TASK-140's ranking at scale.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Spot-check a sample of clinics' registry beds against the source PDF directly, weighted toward TASK-140's ranking outliers (both very-low and suspiciously-high ratio clinics, since either end could be a beds error rather than a real coverage gap)
- [ ] #2 Error rate reported as a number; go/no-go on a full re-check
<!-- AC:END -->
