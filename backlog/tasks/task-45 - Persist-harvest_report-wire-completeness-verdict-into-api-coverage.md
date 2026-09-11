---
id: TASK-45
title: Persist harvest_report + wire completeness verdict into /api/coverage
status: To Do
assignee: []
created_date: '2026-09-11 05:01'
labels:
  - harvester
dependencies: []
ordinal: 45000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
18-agent completeness workflow (2026-09-10/11) proved 150/220 boards green (281/407 clinics, 42554/63358 beds) but the result lives only in /tmp -- pflege_jobs.harvest_report does not exist in Postgres and data/feature_cells.jsonl is 0 bytes, so /api/coverage.feature_score is null on every row and /pro shows 'nc' everywhere. Full per-board verdict table is in the workflow's judgment output (this session, 2026-09-11).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 harvest_report table exists in pflege_jobs, populated from the completeness run
- [ ] #2 /api/coverage returns a real per-board verdict {green|red, reason, checked_at} instead of null feature_score
<!-- AC:END -->
