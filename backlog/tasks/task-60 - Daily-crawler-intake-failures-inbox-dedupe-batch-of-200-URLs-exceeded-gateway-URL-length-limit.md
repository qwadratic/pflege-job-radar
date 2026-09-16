---
id: TASK-60
title: >-
  Daily crawler intake failures: inbox dedupe batch of 200 URLs exceeded gateway
  URL-length limit
status: To Do
assignee: []
created_date: '2026-09-16 15:42'
updated_date: '2026-09-16 15:42'
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

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed and shipped (commit 0b2204e): app/crawl.py's _post_inbox dedupe-lookup batch size cut from 200 to 50, matching the identical convention already used elsewhere in this codebase for the same URL-length failure mode. Verified live against a real 204-URL batch: reliable failure at 200, zero failures at 50. AC#3 (monitor recurrence) is intentionally left unchecked -- can't be proven same-day, needs the next few scheduled runs to confirm.
<!-- SECTION:FINAL_SUMMARY:END -->
