---
id: TASK-92
title: >-
  Daily intake has failed on every scheduled run since 2026-09-19: statement
  timeout, then inbox write quota exhausted (n_new=0 for three days)
status: To Do
assignee:
  - '@claude'
created_date: '2026-09-21 06:47'
updated_date: '2026-09-21 07:59'
labels: []
dependencies: []
ordinal: 92000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the 2026-09-21 review of TASK-60. TASK-60 fixed the URL-length side of the inbox dedupe lookup (chunk 50) and the two in-repo defects that hid the rest (rest_get discarding the response body; _post_inbox posting an unchecked batch). It did NOT end the intake failures, and the failure is ongoing in production.

Record from data/app.sqlite crawl_runs/run_log, every full scheduled run since chunk=50 landed on 2026-09-16 (commit 0b2204e):
  run 96  2026-09-18 adapter  intake OK, 19 new
  run 100 2026-09-19 adapter  intake FAILED PostgREST 500 {"code":"57014","message":"canceling statement due to statement timeout"}, preceded by 19 'inbox dedupe lookup failed (400 ...)' lines
  run 101 2026-09-19 verify   same 57014, 17 dedupe 400s
  run 104 2026-09-20 adapter  same 57014, 18 dedupe 400s
  run 105 2026-09-20 verify   PostgREST 400 {"code":"P0001","message":"inbox: daily limit reached for this client"}, 17 dedupe 400s
  run 108 2026-09-21 adapter  same P0001, ZERO dedupe 400s -- refused on the very first insert
n_new has been 0 on every run since 2026-09-18, i.e. nothing crawled in the last three days has reached postings.

Live inbox table read-only today (18192 rows, 10799 distinct source_url):
  2026-09-19  1000 rows received, 952 of them a source_url already present from an earlier day
  2026-09-20  2000 rows received by client 'vendor-adapters-default' -- an exact round number, i.e. the ceiling itself -- 1803 of 2000 already present from an earlier day
  2026-09-21  0 rows received, yet run 108 was refused with P0001 at 05:53. So the limit is not a calendar-day count of landed rows; it behaves like a rolling window or counts attempts rather than inserts.

Two things are open. (1) Why the dedupe GETs return 400 at all: their bodies were discarded by the pre-fix rest_get, so only the request URL was logged. It is NOT URL length (chunk-50 filters built from the same hosts replay clean today at ~6.9KB against a measured ~25KB gateway threshold) and it is NOT 'the quota 400s every request from that client' (ordinary inbox GETs with the same anon key return 200 right now, after run 108 tripped the quota). The first scheduled run on the fixed rest_get will log the real body. (2) The quota itself lives server-side in Supabase and there is no Supabase access token in this environment to read or raise it (standing limitation, TASK-76 AC#3).

The 57014 statement timeout is a separate shape from the quota and has never been explained: it fired on the inbox insert on three consecutive runs while the dedupe was still being swallowed, i.e. while whole runs were being re-posted unchecked.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The response body of the failing inbox dedupe GET is captured from a real scheduled run and the 400 is root-caused (not inferred)
- [ ] #2 The PostgREST 57014 statement timeout on the inbox insert is reproduced or ruled out, with evidence, and its cause named
- [ ] #3 The inbox daily write limit for client vendor-adapters-default is read from the server side (value, window, what it counts) or the blocker on reading it is recorded with what access is missing
- [ ] #4 A scheduled run completes its intake step end to end with n_new reflecting real new postings, evidenced from crawl_runs
- [ ] #5 A run whose intake step fails is visible without reading run_log: the failure is recorded as a crawl_issue and surfaced in the daily report
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Additional evidence gathered independently 2026-09-21 (was filed separately as TASK-93, now archived as a duplicate of this task):

What disguised this. The database is NOT fully frozen, which is why totals still looked alive: postings.first_seen shows 13 new rows on 09-21, 74 on 09-20, 103 on 09-17. But those timestamps cluster at 00:02, which is the small mode=firecrawl runs (106/107). The 03:00 adapter run's several thousand rows never arrive. The bulk path is dead while the trickle path works.

Run 108 exact failure: crawled 12,251 rows, dropped 5,976 as non-nursing before the insert, then
  intake FAILED RuntimeError: PostgREST 400: {"code":"P0001","message":"inbox: daily limit reached for this client"}
app/crawl.py:436 already documents the cause: the quota was survivable when the schedule crawled a 1/7 slice per day and became unsurvivable when it went to the whole registry daily. TASK-73's classify-before-insert cut volume by 83% and was still not enough.

The P0001 trigger that raises this is NOT in sql/ -- sql/010_inbox.sql defines the table with no such rule -- so the cap lives server-side in Supabase and was never captured in the repo's schema.

Directions worth evaluating rather than assuming, now that a Supabase access token exists:
(a) adapter rows may not need the inbox at all -- sql/010_inbox.sql documents it as the browser-collector queue, and seeded adapters already write observations straight through EdgeSink;
(b) raise or remove the server-side cap (this is TASK-23's "remove the token-blocked framing");
(c) enqueue only rows the dedupe lookup proves are new.
(a) and (c) compose.

Extra acceptance criteria folded in from the duplicate:
- The chosen approach is justified in writing against those alternatives, including whether adapter rows should use the inbox queue at all.
- An intake failure can never again be invisible: a run whose intake failed must not report rows crawled as though they landed.
- After the fix, re-run the full adapter crawl and report n_new plus the new open_jobs total against the 2560 baseline of 2026-09-21.
<!-- SECTION:NOTES:END -->
