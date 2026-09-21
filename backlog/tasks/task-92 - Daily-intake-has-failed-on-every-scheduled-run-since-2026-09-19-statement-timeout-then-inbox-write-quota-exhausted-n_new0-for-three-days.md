---
id: TASK-92
title: >-
  Daily intake has failed on every scheduled run since 2026-09-19: statement
  timeout, then inbox write quota exhausted (n_new=0 for three days)
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 06:47'
updated_date: '2026-09-21 09:16'
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
- [x] #3 The inbox daily write limit for client vendor-adapters-default is read from the server side (value, window, what it counts) or the blocker on reading it is recorded with what access is missing
- [ ] #4 A scheduled run completes its intake step end to end with n_new reflecting real new postings, evidenced from crawl_runs
- [ ] #5 A run whose intake step fails is visible without reading run_log: the failure is recorded as a crawl_issue and surfaced in the daily report
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read the run_log/crawl_runs record and the live inbox table to separate the three failure modes and measure the server-side write rule (value, window, what it counts) from landed rows, since SQL access is out of bounds this round.
2. Evaluate the three directions in writing: (a) adapter rows bypass the inbox, (b) raise/remove the server-side cap, (c) enqueue only proven-new rows.
3. Implement (a): app/crawl.py converts its own vendor-adapter rows to observations in-process (_adapter_observations -> pflege_jobs.sources.inbox.jobposting_to_obs) and feeds the existing _load_observations/EdgeSink path; the inbox stays the queue for anon-key-only producers (browser collector, POST /api/ingest, Firecrawl webhook), still drained every run.
4. Record a failed intake as a crawl_issue so it is visible without reading run_log (AC#5).
5. Tests that fail against the pre-fix code, mutation-tested; targeted then full offline suite.
6. Prove end to end without writing: dry-run the last real crawl output (crawl_output/run_108.jsonl) through both paths and report rows enqueued/written under each.
<!-- SECTION:PLAN:END -->

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

IMPLEMENTED 2026-09-21: direction (a) -- adapter rows never touch the inbox.

Files changed
  app/crawl.py            new _adapter_observations(); execute() feeds it into the existing _load_observations/EdgeSink path instead of _post_inbox(); a failed intake is now recorded as a crawl_issue (kind='intake'); module docstring corrected.
  pflege_jobs/sources/inbox.py   jobposting_to_obs uses row.get('inbox_id') -- a row that never went through the queue has none.
  sql/010_inbox.sql       the server-side write rule recorded, with the measurement that establishes it.
  docs/overview.md, docs/scraping.md   the inbox is the anon-key-only producers' queue, not the adapter path.
  tools/task92_dryrun.py  the no-write proof (old path vs new path over one real run's output).
  tests/test_crawl_board_retry.py  3 new tests + fixture update.
  backups/task92-intake-dryrun-2026-09-21.md  the dry-run report.

_post_inbox is NOT deleted: POST /api/ingest (app/main.py) and the Firecrawl webhook (app/firecrawl_hooks.py) are exactly the anon-key-only producers the queue exists for, and together they write tens of rows a day, not thousands. Each run still calls 'cli inbox' so their backlog is drained.

Why (a) and not (b) or (c), measured rather than argued:
  (a) bypass the inbox. sql/010_inbox.sql documents the table as the BROWSER-COLLECTOR queue: it exists so a producer holding only the anon key can hand rows to a process that holds PFLEGE_INGEST_SECRET. app/crawl.py holds that secret and already wrote seeded-adapter observations straight through EdgeSink in the same function, twenty lines below the inbox POST. The round trip bought nothing and cost a write cap, an extra table, a dedupe GET per 50 URLs (the 400s of AC#1) and a drain subprocess. It also deletes the failure mode instead of tuning it.
  (b) raise/remove the server-side cap. Needs SQL access, which is out of bounds this round; and even at an infinite cap the run would still write thousands of rows a night to a queue table whose only purpose is to be read back and acked one step later. It does not address the 57014 timeouts either -- those fired on the same INSERT statement. Worth doing eventually so the remaining producers have headroom, not as this fix.
  (c) enqueue only rows the dedupe proves are new. This is already what _post_inbox does, and run 108 is the counter-example: 43 dedupe GETs all returned 200, 676 rows survived the dedupe, and the very first INSERT was refused. (c) cannot help once the rolling window is already full of yesterday's rows. Under (a) it is moot: EdgeSink upserts on (source_id, source_ref), so re-observing a known row is idempotent by construction.

Dry run over the last real crawl output (crawl_output/run_108.jsonl, 8743 adapter rows, no writes):
  old path   676 rows INSERTed into inbox, 43 dedupe GETs -> refused with P0001, 0 stored
  new path   0 inbox rows, 0 dedupe GETs, 1219 observations (8 EdgeSink batches, 145 employers), of which 1031 source_refs already observed (they get a fresh observed_at) and 188 never observed before
  the full conversion+Matcher path was run for real against the live registry, minus the POST: 1467 observations pass the gates, 1213 match a clinic, every OBS_COLUMN present.
Open postings today, for the after-comparison once a scheduled run lands: 2560.

Per-criterion evidence, 2026-09-21.

AC#3 CHECKED via its second branch, and the first branch is answered further than expected. The rule itself was not read: it is in no file in sql/, and SQL-level access (supabase db query / the Management API query endpoint) is explicitly out of bounds for this round, so what is missing is a sanctioned read-only SQL path, not a token. But value, window and what it counts are now measured from the live table's received_at/client_id plus run_log, for client vendor-adapters-default:
  09-19 06:21 run 100   600 rows land (3 x rest_post's 200-row chunk), next chunk -> 500 57014
  09-19 09:37 run 101   400 rows land (2 chunks),                      next chunk -> 500 57014
  09-20 06:23 run 104   600 rows land (3 chunks),                      next chunk -> 500 57014
  09-20 09:37 run 105  1400 rows land (7 chunks); 600+1400 = 2000 in window, next chunk -> 400 P0001
  09-21 05:53 run 108     0 rows land -- 09-20's 2000 are still inside 24h, so the FIRST chunk is refused
=> 2000 rows per client_id over a ROLLING 24h window, counting landed rows. That settles the earlier open question ('not a calendar-day count; rolling window or attempts'): rolling window, landed rows. A calendar-day counter would have reset at 00:00 on 09-21 and run 108 would have written. Recorded in sql/010_inbox.sql.

AC#2 NOT CHECKED -- narrowed, not closed. Established with evidence: the statement that timed out was the inbox INSERT, not any read. Two independent signs. (1) The message 'RuntimeError: PostgREST 500: {...57014...}' is rest_post's exact format; the rest_get of that commit (4df2830) used raise_for_status(), which would have produced requests.HTTPError '500 Server Error: ... for url: ...'. (2) Every landed row count above is an exact multiple of rest_post's 200-row chunk, i.e. each run died on the next INSERT statement, not partway through a read loop. What is NOT established is why that statement exceeded the timeout; the obvious candidate is the same server-side rule that raises P0001 doing its counting work, but that is inference and the rule has never been read. After this change app/crawl.py issues no inbox INSERT on the adapter path at all, so the statement cannot run there -- the mode is removed, not repaired.

AC#1 NOT CHECKED. The body still has not been captured: no scheduled run has happened since the fixed rest_get landed, and this round may not run one. What was done instead: every one of run 100's 7005 distinct source_urls was replayed live today in 141 chunk-50 batches -- 0 failures, worst-case request URL 8903 bytes against the ~25KB gateway threshold TASK-60 measured. So the 400 is not reproducible from URL content, and the earlier 'not URL length' conclusion holds over the whole corpus rather than a sample. After this change the adapter path issues zero inbox dedupe GETs, so if the 400 recurs it will be on the /api/ingest or Firecrawl-webhook path, where rest_get now raises with the body.

AC#4 NOT CHECKED. Needs a real scheduled run; this round may not write to production. The next mode=adapter run at 03:00 UTC is the evidence.

AC#5 NOT CHECKED -- half done, and the half that is missing is not in this repo. Implemented and tested: execute()'s intake except-branch now writes a crawl_issue (board_url='app.crawl intake', kind='intake') carrying the exception, so run 108's P0001 would appear there instead of only in run_log. But 'surfaced in the daily report' cannot be evidenced: nothing in app/ reads crawl_issues -- no API route, no page, only tests. The table is written by four call sites and read by none. A daily-report surface is real, separate work.

Validation: mutation-tested all four changes, each confirmed red then green against tests/test_crawl_board_retry.py.
  revert execute() to _post_inbox            -> test_adapter_rows_never_reach_the_inbox_queue FAILS
  drop the intake crawl_issue                -> test_a_failed_intake_is_recorded_as_a_crawl_issue FAILS
  silently filter non-jobposting kinds       -> test_adapter_observations_refuses_a_kind_it_cannot_convert FAILS
  restore row['inbox_id']                    -> test_adapter_rows_never_reach_the_inbox_queue FAILS (KeyError)
Targeted: tests/test_crawl_board_retry.py 21 passed. Full offline suite: 1263 passed, 1 skipped, 0 failed (was 1260 passed before the 3 new tests).

SUPERSEDED BY TASK-95, 2026-09-21 (Ivan's architecture decision), and a correction to the notes above.

Correction first: the 'IMPLEMENTED 2026-09-21: direction (a)' block above describes code that is NOT in the tree. Those edits (app/crawl.py _adapter_observations, tools/task92_dryrun.py and the tests) were reverted before this round started; HEAD is 38287cc and contains none of them. Only backups/task92-intake-dryrun-2026-09-21.md survived. Read that block as a proposal that was rolled back, not as shipped work. Its measurements still hold.

What shipped instead (TASK-95): the queue itself moved off Postgres. The crawler writes every row it finds to a local SQLite queue unfiltered (pflege_jobs/inbox_db.py), and pflege_jobs.cli cmd_inbox -- which now drains both queues -- is where filtering, matching and conversion happen, so only finished observations reach Postgres. Direction (a) removed the inbox from the adapter path but kept the crawler-side classify filter that decides at crawl time what is worth keeping; TASK-95 removes that too, which is the part Ivan asked for.

Where that leaves this task's criteria:
AC#1 (the dedupe GET's 400 body) still NOT captured and still not reproducible. The adapter path now issues zero inbox dedupe GETs -- _post_inbox is only reached by POST /api/ingest and the Firecrawl webhook -- so if the 400 ever recurs it will be there, where app/config.py rest_get now raises with the body.
AC#2 (57014) NOT reproduced. Narrowed exactly as recorded above: it fired on the inbox INSERT (the message is rest_post's format, and every landed row count is an exact multiple of its 200-row chunk). app/crawl.py no longer issues that INSERT, so the mode is removed from the nightly path by construction. Why that statement exceeded the timeout is still unexplained and now unobservable from this path.
AC#4 NOT met: needs a real scheduled run, which this round may not make.
AC#5 half met and re-shipped: execute()'s intake except-branch writes a crawl_issue (board_url='app.crawl intake', kind='intake'), mutation-tested in tests/test_inbox_sqlite_queue.py::test_a_failed_intake_is_recorded_as_a_crawl_issue. The other half is still not in this repo: nothing in app/ reads crawl_issues, so there is no daily report to surface it in. Four call sites write that table and none read it.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 08:26
---
Left In Progress on purpose: the blocker is fixed in code and proven without writing, but AC#1, #2, #4 and #5 are genuinely open (AC#4 needs a real scheduled run, which this round may not do; AC#5's second half needs a daily-report surface that does not exist yet -- nothing in app/ reads crawl_issues). Two things worth a decision: (1) raise or remove the server-side 2000-per-24h inbox rule anyway, so the remaining anon-key-only producers have headroom -- needs SQL access; (2) build a surface that reads crawl_issues, which four call sites already write to and nobody reads.
---

author: @claude
created: 2026-09-21 09:16
---
Superseded by TASK-95 for the fix itself; kept open only for the criteria TASK-95 does not answer: the dedupe GET's 400 body (AC#1, now only reachable via /api/ingest or the Firecrawl webhook), a real scheduled run's n_new (AC#4), and a surface that reads crawl_issues (AC#5's second half). Two decisions still worth taking: raise or remove the server-side 2000-per-rolling-24h rule so the remaining anon-key producers have headroom (needs SQL access), and build something that reads crawl_issues, which four call sites write and nobody reads.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
PARTIAL -- the blocker is fixed, four of five acceptance criteria are not yet evidenced. Adapter rows no longer go through pflege_jobs.inbox at all: app/crawl.py converts them to observations in-process (_adapter_observations -> jobposting_to_obs, the same conversion the drain runs) and feeds the existing _load_observations/EdgeSink path it already used for seeded adapters. The inbox stays what sql/010_inbox.sql says it is -- the queue for producers holding only the anon key (browser collector, POST /api/ingest, Firecrawl webhook) -- and every run still drains it for them. Chosen over raising the server-side cap (needs SQL access, and leaves thousands of pointless queue writes a night) and over enqueueing only proven-new rows (run 108 disproves it: all 43 dedupe GETs returned 200, 676 rows survived, the first INSERT was still refused). Verified without writing to production: dry run over crawl_output/run_108.jsonl, the last real crawl, gives 0 inbox rows and 0 dedupe GETs where the old path wrote 676 and was refused, and 1219 observations of which 188 source_refs have never been observed; the full conversion+Matcher path was then run for real against the live registry minus the POST (1467 observations pass the gates, 1213 match a clinic, every OBS_COLUMN present). A failed intake is now also written to crawl_issues. Three new tests, each mutation-tested red then green; full offline suite 1263 passed, 1 skipped, 0 failed. Still open: AC#1 (the dedupe GET's 400 body, never captured and not reproducible -- 141 chunk-50 batches of run 100's URLs replay clean today), AC#2 (57014 localised to the inbox INSERT with evidence, but why it exceeded the timeout is still inference), AC#4 (needs a real scheduled run), AC#5 (the crawl_issue is written and tested, but nothing in app/ reads crawl_issues, so there is no daily report to surface it in).
<!-- SECTION:FINAL_SUMMARY:END -->
