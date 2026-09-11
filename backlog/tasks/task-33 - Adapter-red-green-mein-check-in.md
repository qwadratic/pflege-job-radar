---
id: TASK-33
title: 'Adapter red-green: mein-check-in'
status: To Do
assignee: []
created_date: '2026-09-10 07:49'
labels:
  - harvester
dependencies: []
ordinal: 33000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
One adapter at a time, per Ivan's method (2026-09-10). Write the red completeness tests for every board this adapter serves, run them, fix the adapter until green (every sidebar group read, section labels kept), then break it on purpose and confirm the tests go red naming this adapter. Do not skip Playwright where plain HTTP fails; Firecrawl may be used as an oracle (about 3000 credits available, more on request). Every fetched page is snapshotted.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All completeness checks for mein-check-in are green on every board it serves in the live registry
- [ ] #2 Each of the four mutations turns exactly the matching check red for mein-check-in
- [ ] #3 Rows returned carry description, city, dates and a browsable url wherever the source exposes them
<!-- AC:END -->
