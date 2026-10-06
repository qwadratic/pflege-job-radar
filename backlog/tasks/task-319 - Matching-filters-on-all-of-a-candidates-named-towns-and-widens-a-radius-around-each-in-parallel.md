---
id: TASK-319
title: >-
  Matching filters on all of a candidate's named towns and widens a radius
  around each in parallel
status: To Do
assignee: []
created_date: '2026-09-26 13:16'
labels: []
dependencies:
  - TASK-1
priority: medium
ordinal: 261000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan (2026-09-26): when a candidate names several towns (nurse 79: Nürnberg and Erlangen), matching filters on all of them at once through the board's multi-city filter (app/data.py filter_jobs city=a,b). When that gives fewer clinics than needed, the radius widens around every named town together, one step at a time, never around one town only. The board API has no radius parameter today (TASK-1). The nurse-79 case did it offline from posting coordinates (data/email-analysis/cases/nurse-79/pool.py, CITY_RADII 0/10/20/30/40/50/60/80/100/125/150/175/200/250 km), which is the behaviour to reproduce in the harness.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A candidate with two or more named towns is matched against postings in all of them in one filter
- [ ] #2 When the result is below the requested count, each step widens the radius around every named town by the same amount and reports the step it stopped at
- [ ] #3 The stop condition is the requested count or the last step, reported as such; no silent cut
- [ ] #4 Tests cover one town, two towns, and a case where only the widest step reaches the count
<!-- AC:END -->
