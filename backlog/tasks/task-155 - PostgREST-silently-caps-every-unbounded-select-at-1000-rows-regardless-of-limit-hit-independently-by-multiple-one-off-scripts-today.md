---
id: TASK-155
title: >-
  PostgREST silently caps every unbounded select at 1000 rows regardless of
  limit=, hit independently by multiple one-off scripts today
status: To Do
assignee: []
created_date: '2026-09-24 17:33'
updated_date: '2026-09-25 00:10'
labels:
  - db-quality
  - infra
dependencies: []
ordinal: 155000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found twice independently today (2026-09-24, TASK-142's and TASK-154's own investigations, unrelated to each task's actual scope): a plain GET against the Supabase PostgREST endpoint with limit= set higher than 1000 (or no limit= at all) silently returns only the first 1000 rows with a 200 status and no error -- Content-Range: 0-999/* confirms it, but nothing in a typical one-off script checks Content-Range, so the truncation is invisible. TASK-142's agent got bitten mid-investigation and had to add Range-header pagination to get the true row count (3638 open postings, not a silently-truncated ~1000). TASK-154's live verification run hit the same cap again in its own diagnostic 'total now 1000' output and flagged it rather than fixing it (out of that task's scope). This has likely silently under-counted or under-processed data in other places across the session's many one-off data/*.py and tools/*.py scripts that do a single unbounded REST GET, not just the two instances caught today.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Grep the repo for direct PostgREST GET calls (data/*.py, tools/*.py, crawlers/*.py -- the requests.get(...rest/v1/...) pattern) and list every one that does not already paginate via Range headers or OFFSET
- [ ] #2 Decide and apply a standard fix: either a small shared helper (paginated REST GET) that these scripts import, or a documented convention (always check Content-Range, always paginate above 1000) -- whichever is the smaller true fix, not a new abstraction for its own sake
- [ ] #3 Live-verified: at least one previously-affected script (or a repro case) is confirmed to now return the true full row count above 1000, not a silently truncated 1000
<!-- AC:END -->
