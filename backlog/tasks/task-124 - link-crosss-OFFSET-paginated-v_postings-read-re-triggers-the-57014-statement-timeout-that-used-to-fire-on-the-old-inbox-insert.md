---
id: TASK-124
title: >-
  link-cross's OFFSET-paginated v_postings read re-triggers the 57014 statement
  timeout that used to fire on the old inbox insert
status: To Do
assignee: []
created_date: '2026-09-23 07:55'
labels: []
dependencies:
  - TASK-92
priority: medium
ordinal: 124000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 pflege_jobs/cli.py's cmd_link_cross cross-source dedupe stage (the v_postings GET, currently offset-paginated at limit=1000) is confirmed to re-run pflege_jobs.v_postings' own linked_towns CTE (a GROUP BY over every postings row, sql/012_task105_requirements_fields.sql) on every page, not just the current one -- verify via EXPLAIN or a timed before/after (needs SQL access, TASK-76 AC#3's standing limitation, or a live timing comparison)
- [ ] #2 A concrete fix is chosen and justified against alternatives (keyset/cursor pagination on posting_id instead of OFFSET, a larger page size to cut the number of round trips, materializing linked_towns as its own indexed table/materialized view instead of a CTE recomputed per query, or something else) -- picked with evidence, not the first idea tried
- [ ] #3 Reproduced fixed: a real link-cross run against the live registry completes with no 57014, under conditions similar to today's failure (concurrent crawl load)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Found 2026-09-23 while closing TASK-92 AC#2 (which is about the OLD Postgres-inbox INSERT's own 57014, since ruled out -- that code path no longer exists post-TASK-95). Live evidence this is a DIFFERENT, still-active occurrence of the same PostgREST 57014 signature: run_id 158 (2026-09-23, one of TASK-118's own live recrawls), run_log: `v_postings: HTTP 500 {"code": "57014", ..., "message": "canceling statement due to statement timeout"}` inside pflege_jobs.cli link-cross's own 'same-source url variants' stage, recorded as crawl_issue kind='intake' (board_url='pflege_jobs.cli link-cross'). Same signature also hit 2 of TASK-86's 21 recrawls the same day (16201, 36202), all under heavy concurrent load (multiple background Workflow runs + sequential recrawls hitting Postgres at once) -- not reproduced in isolation, not chased further at the time.

Root cause, precisely: pflege_jobs/cli.py:294-298 fetches pflege_jobs.v_postings (a view, not a table -- sql/012_task105_requirements_fields.sql's own `create or replace view`) in an OFFSET-paginated loop (limit=1000, offset+=1000 per page). The view itself opens with a `linked_towns` CTE that GROUP BYs every row in pflege_jobs.postings by employer_id -- a CTE Postgres cannot push the outer page's WHERE/LIMIT into, since it aggregates over the WHOLE table, unrelated to any single page's rows. Every one of the loop's ~N pages (N = open postings / 1000, currently low teens) therefore re-materializes that full-table GROUP BY from scratch, not just once -- a real, measured cost multiplier that grows with total posting count and gets worse under concurrent DB load, matching every observed instance (always under heavy concurrent load, never reproduced solo).

Not fixed this round: a confident fix needs either SQL-level access (EXPLAIN the view, out of bounds per TASK-76 AC#3) or a live timed before/after this round did not budget for -- guessing at a pagination-shape change to a production data-merge path without being able to verify its real query-plan effect risks a worse regression than the problem itself.
<!-- SECTION:NOTES:END -->
