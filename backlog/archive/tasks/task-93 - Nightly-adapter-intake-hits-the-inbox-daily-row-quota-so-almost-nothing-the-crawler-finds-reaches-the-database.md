---
id: TASK-93
title: >-
  Nightly adapter intake hits the inbox daily row quota, so almost nothing the
  crawler finds reaches the database
status: To Do
assignee: []
created_date: '2026-09-21 07:58'
labels: []
dependencies: []
ordinal: 93000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-21 reading data/app.sqlite run_log directly, not from a report. This is the top blocker: every fix that recovers postings is worthless until it lands.

Scheduled run 108 (2026-09-21T03:00, mode=adapter) crawled 12,251 rows, dropped 5,976 as non-nursing before the insert, and then:
  intake FAILED RuntimeError: PostgREST 400: {"code":"P0001","message":"inbox: daily limit reached for this client"}
  finished with 4 error(s) / run finished: failed, rows 12251
n_new is 0 on run 100, 101, 104, 105 and 108 -- every full scheduled run on 09-19, 09-20 and 09-21.

The database is not fully frozen, which is what disguised this: postings.first_seen shows 13 new rows today, 74 on 09-20, 103 on 09-17 -- but the timestamps cluster at 00:02, which is the small mode=firecrawl runs (106/107). The 03:00 adapter run's several thousand rows never arrive. So the bulk path is dead while the trickle path works, and the totals still move enough to look alive.

app/crawl.py:436 already documents the cause in a comment: the quota was survivable when the schedule crawled a 1/7 slice per day and became unsurvivable when it went to the whole registry every day. Classifying before the insert (TASK-73) cut the volume by 83% and was still not enough.

Note this is a DIFFERENT failure from TASK-60's URL-length 400: that one was the dedupe lookup GET, fixed by chunking at 50. This is the insert itself, rejected by a server-side per-client daily row cap (P0001, raised by a trigger that is not in sql/ -- it is not in sql/010_inbox.sql). The verifying reviewer also found PostgREST 500 '57014 canceling statement due to statement timeout' on runs 100/101/104, so there may be more than one intake failure mode.

Directions worth evaluating rather than assuming: (a) adapter rows do not obviously need the inbox at all -- the inbox is documented in sql/010_inbox.sql as the browser-collector queue, and seeded adapters already write observations straight through EdgeSink, which is not quota-limited the same way; (b) raise or remove the server-side cap now that a Supabase access token exists (this is TASK-23's 'remove the token-blocked framing'); (c) only enqueue rows that the dedupe lookup shows are genuinely new. (a) and (c) compose.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A full scheduled adapter run over the whole registry completes intake without hitting a quota or a statement timeout, and n_new reflects the real number of new postings
- [ ] #2 The chosen approach is justified in writing against the alternatives above, including whether adapter rows should use the inbox queue at all
- [ ] #3 Every intake failure mode seen in the log is covered, not just the quota: the P0001 daily cap AND the PostgREST 500 57014 statement timeout on runs 100/101/104
- [ ] #4 An intake failure can never again be invisible: a run whose intake failed must not report rows crawled as though they landed, and the daily report must show it
- [ ] #5 After the fix, re-run the full adapter crawl and report n_new, plus the new open_jobs total against the 2560 baseline of 2026-09-21
<!-- AC:END -->
