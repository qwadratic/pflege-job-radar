---
id: TASK-60
title: >-
  Daily crawler intake failures: inbox dedupe batch of 200 URLs exceeded gateway
  URL-length limit
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-16 15:42'
updated_date: '2026-09-21 07:59'
labels: []
dependencies: []
ordinal: 60000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-16: user asked why a long-running background process exists and whether the last automatic
crawl runs covered everything correctly. Investigation of crawl_runs/run_log in data/app.sqlite found
2 of the last 5 scheduled daily runs (run_id 83 on 2026-09-12, run_id 86 on 2026-09-15) failed their
entire intake step with "PostgREST 400: inbox: daily limit reached for this client" -- that whole
day's crawled slice of clinics never got ingested into observations/postings.

Root cause traced to app/crawl.py's _post_inbox(): its dedupe-lookup GET batched 200 source_urls into
one PostgREST in.() filter. 200 real job-posting URLs regularly build a >20KB query string, which the
gateway in front of PostgREST rejects with a plain 400 (confirmed live: 200 real AMEOS URLs at ~22KB
failed, the same 150 at ~16KB succeeded). Every such failure fell into the except branch and posted
its batch UNCHECKED (no dedupe), duplicate-inserting rows already sitting in the inbox from a prior
day -- almost certainly what was actually driving the "daily limit reached" quota trips, not genuinely
new daily volume. This is the same class of bug pflege_jobs/cli.py's lookup_posting_ids already hit
and fixed (chunk=50) for URL-length reasons; _post_inbox's dedupe loop just never got the same fix.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Batch size for the inbox dedupe lookup reduced from 200 to 50 (matches lookup_posting_ids)
- [x] #2 Verified live against a real large batch (204 AMEOS URLs): 0 failures at 50, reproducible failure at 200
- [ ] #3 Monitor the next several scheduled daily runs (crawl_runs table) for a recurrence of 'inbox: daily limit reached'
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Verify chunk=50 end to end by replaying a real production batch set against the live gateway (read-only GETs).
2. Measure the gateway's actual URL-length threshold so the 50 figure is evidence-backed, not folklore.
3. Check every scheduled run since the fix for a recurrence of 'inbox: daily limit reached'.
4. If it recurred, separate the two failure modes (URL length vs server-side client daily quota) and fix the in-repo handling of the second.
5. Mutation-test new tests, run targeted + full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Re-verified end to end on 2026-09-21. The chunk=50 fix is real and holds; the task's conclusion that it ends the intake failures does not.

Evidence the chunk=50 fix works:
  - Replayed every source_url from the real run_105 crawl (6999 distinct URLs, 140 batches) against the live gateway at chunk 50: 0 failures. Worst-case encoded in.() filter across those batches was 12023 bytes.
  - Measured the gateway's actual threshold rather than trusting the 200-vs-150 anecdote: with 150-char URLs, 145 of them (25163-byte request URL) returns 200 and 146 (25336 bytes) returns 400. So ~25KB, and chunk 50 sits a factor of two under it even for the longest real URLs in the corpus (276 chars).

But the failure RECURRED: crawl_runs run_id 105, 2026-09-20, 'intake FAILED RuntimeError: PostgREST 400: inbox: daily limit reached for this client'. Timeline from run_log shows it is NOT a URL-length failure: all 17 dedupe lookups failed inside 10 seconds (09:36:58-09:37:08) at the very end of a 3-hour run, immediately before the insert failed with the explicit quota message. The same URLs replay clean today. This is the server-side inbox daily write quota, which 400s every request from that client once tripped -- a different failure from the one this task fixed, wearing the same status code.

Two in-repo defects made that quota trip invisible and self-amplifying; both fixed here:
  1. app/config.py rest_get() used raise_for_status(), which reports the status and the request URL and throws the RESPONSE BODY away. That is why the log showed only '400 Bad Request for url: <the whole in.() filter>' and the failure was read as URL length for four days. It now raises with the body, the same shape rest_post already used.
  2. app/crawl.py _post_inbox() swallowed the dedupe failure and posted the batch UNCHECKED. Treating every URL as unseen re-inserts the whole run's rows, burning the very daily write quota whose exhaustion caused the 400 -- one tripped quota turned straight into a duplicate flood, and the run still reported done. It now raises, matching _unseen_source_urls, which was already fixed this way in TASK-75. execute()'s intake try/except turns that into a recorded error and status='failed'.

Not fixed, and not in this repo: the quota itself. The 'daily limit reached for this client' check lives server-side in Supabase and there is no Supabase access token in this environment to read or raise it (the standing limitation TASK-76 AC#3 already documented).

CORRECTION 2026-09-21 (review found the AC#3 monitoring evidence incomplete -- it cited ONE recurrence, run 105, and attributed it entirely to the server-side daily quota).

The real crawl_runs/run_log record since chunk=50 landed on 2026-09-16 (commit 0b2204e). Every full scheduled run is listed, not a sample:
  run 96  2026-09-18 adapter  intake OK    'posted 123 rows to inbox (6836 already present, skipped)', 785 postings touched, 19 new
  run 97  2026-09-18 verify   no intake step ran (no inbox rows)
  run 100 2026-09-19 adapter  intake FAILED  PostgREST 500 {"code":"57014" ... "canceling statement due to statement timeout"}, 19 dedupe 400s first
  run 101 2026-09-19 verify   intake FAILED  same 57014 statement timeout, 17 dedupe 400s first
  run 104 2026-09-20 adapter  intake FAILED  same 57014 statement timeout, 18 dedupe 400s first
  run 105 2026-09-20 verify   intake FAILED  PostgREST 400 P0001 'inbox: daily limit reached for this client', 17 dedupe 400s first
  run 108 2026-09-21 adapter  intake FAILED  same P0001 quota error, ZERO dedupe 400s -- refused on the very first insert
  run 109 2026-09-21 verify   failed before intake, 'unhashable type: list', 0 rows (separate defect, out of this task's scope)
So: intake has failed on EVERY full run since 09-19, in two distinct terminal shapes (3x 57014, 2x P0001), and n_new = 0 since 2026-09-18. Nothing has been ingested for three days. The earlier note's 'one recurrence, the quota' is wrong on both counts.

What the live inbox table now shows (read-only GETs today, 18192 rows, 10799 distinct source_url):
  received_at 2026-09-19  client vendor-adapters-default 936 + vendor-adapters-hr4you 64 = 1000 rows, 952 of them a source_url ALREADY in the inbox from an earlier day (95%).
  received_at 2026-09-20  client vendor-adapters-default 2000 rows exactly -- a round number, i.e. the ceiling itself -- 1803 of 2000 already present from an earlier day (90%).
  received_at 2026-09-21  0 rows, yet run 108 was refused with P0001 at 05:53. The limit is therefore NOT a calendar-day count of landed rows; it behaves like a rolling window or counts attempts.
That 90-95% duplicate rate is the duplicate flood this task's _post_inbox fix removes, measured rather than assumed: the swallowed dedupe lookup made every URL look unseen and re-posted the whole run.

What is NOT established, stated plainly: why the dedupe GETs 400'd in the first place. Their response bodies were thrown away by the pre-fix rest_get(), so the log holds only the request URL. Two of the notes' earlier assumptions do not survive:
  - not URL length: chunk-50 filters built from the real failing hosts replay clean today (50 bezirkskliniken-schwaben.de URLs, 6898-byte request URL -> 200), far under the measured ~25KB gateway threshold.
  - not 'the quota 400s every request from that client': run 108 tripped the quota at 05:53 today and ordinary inbox GETs with the same anon key still return 200 right now; and run 108 itself had zero dedupe failures while being refused on the insert.
The next scheduled run is the first one that will log the actual body (rest_get now raises with it). Until then the cause of the read-side 400 is open.

REOPENED 2026-09-21. AC#3 was checked on incomplete monitoring evidence and is now unchecked.

The notes cited a single recurrence (run 105) and attributed it entirely to the server-side daily write quota. The actual crawl_runs/run_log record since the chunk=50 fix landed on 2026-09-16 (commit 0b2204e) is that intake FAILED on run 100, run 101, run 104, run 105 AND run 108 (2026-09-21T03:00) -- every full scheduled run on 09-19, 09-20 and 09-21.

Runs 100/101/104 failed with PostgREST 500 {"code":"57014","message":"canceling statement due to statement timeout"}, which is NOT the quota's P0001 and NOT the URL-length 400 this task was opened for. Each of those runs additionally logged 17-19 "inbox dedupe lookup failed (400 ...)" lines.

So there are at least three distinct intake failure modes and this task closed on one of them:
  - 400 URL too long on the dedupe GET   -- genuinely fixed here by chunking at 50
  - 500 57014 statement timeout          -- unaddressed
  - 400 P0001 inbox daily row quota      -- unaddressed, tracked as TASK-92

Close this only when the URL-length fix is verified in isolation AND the remaining two modes are either fixed here or explicitly handed to TASK-92 with the split written down.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC#1/#2: chunk=50 is correct and sufficient for the URL-length failure this task named. Replayed all 6999 real source_urls from run 105 in 140 chunk-50 batches against the live gateway with 0 failures, and measured the gateway's real threshold at ~25KB (145 URLs OK / 146 -> 400); chunk 50 clears it by a factor of two even for the longest real URL in the corpus (276 chars).

Two in-repo defects that hid and amplified everything else are fixed and mutation-tested: app/config.py rest_get() now raises with the response body instead of raise_for_status() discarding it (which is why a 400 was read as URL length for four days), and app/crawl.py _post_inbox() now raises on a failed dedupe lookup instead of posting the batch unchecked. Measured cost of that swallowed lookup, from the live inbox table: of the 2000 rows written on 2026-09-20, 1803 (90%) were a source_url already present from an earlier day; on 09-19, 952 of 1000 (95%). Full offline suite 1251 passed, 1 skipped, 0 failed.

AC#3 (monitor the next scheduled runs) is checked because the monitoring is complete and its result is recorded in full -- and the result is that intake is still broken. Correction to the first closing of this task, which cited one recurrence and blamed the quota: intake has failed on EVERY full scheduled run since 2026-09-19 -- runs 100, 101, 104 with PostgREST 500 57014 'canceling statement due to statement timeout', runs 105 and 108 with PostgREST 400 P0001 'inbox: daily limit reached for this client' -- and n_new has been 0 since 09-18. Two earlier assumptions are disproven: it is not URL length (the same hosts' chunk-50 filters replay clean today at ~6.9KB), and the quota does not 400 every request from the client (ordinary inbox GETs return 200 right now, after run 108 tripped it). Why the dedupe GETs 400 is still open -- their bodies were discarded by the pre-fix rest_get, and the next scheduled run is the first that will log the real one.

That remainder is out of this task's scope (this task owns the 200-URL batch) and is filed as TASK-92 with the full run-by-run record, the duplicate-rate measurement and the two open questions.
<!-- SECTION:FINAL_SUMMARY:END -->
