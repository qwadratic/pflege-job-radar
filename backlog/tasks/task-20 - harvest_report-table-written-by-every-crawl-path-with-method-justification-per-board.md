---
id: TASK-20
title: >-
  harvest_report table written by every crawl path, with method justification
  per board
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
labels:
  - harvester
dependencies: []
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's rules (2026-09-09): long comments in code are a red flag; concrete measurements, incidents and suspicious findings belong in a report table that the crawler updates while it runs, so a current report over current data always exists; and every algorithm choice per site carries a justification (why plain HTTP, why Playwright, why a feed) -- for example 'robots.txt disallows /*json*, HTML path allowed, plain HTTP kept'. One row per board per load: rung, result (ok, zero_rows, error, walled, dead, truncated), rows, suspicious findings with the shortest decisive evidence line, problems, non-problems, measured numbers, method_reason, note. Zero rows is never ok. Latest row per board is the site status; incidents are a view over results. It must be written from both the app's execute() branches and on snapshot-folder close, so subprocess runners cannot bypass it (critique finding F8). Lives in SQLite via app/runs.py until the Supabase token arrives, then mirrored.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every crawl path, including subprocess runners, writes one harvest_report row per board per load
- [ ] #2 A board with zero rows is recorded as zero_rows, never ok
- [ ] #3 Each row carries method_reason explaining why that rung or source was used
<!-- AC:END -->
