---
id: TASK-95
title: >-
  Move the inbox to SQLite and stop filtering inside the crawler: keep every row
  raw, load only clean rows into Postgres
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 08:38'
updated_date: '2026-09-21 09:16'
labels: []
dependencies: []
ordinal: 95000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's design decision, 2026-09-21, superseding the direction TASK-92 was originally briefed with.

THE PROBLEM. The Postgres inbox carries a server-side write cap. Measured precisely (evidence in TASK-92 notes): 2000 rows per client_id over a ROLLING 24h window, counting landed rows -- not a calendar-day counter, which is why run 108 was refused on its FIRST chunk while 09-20's 2000 rows were still inside the window. Intake has therefore failed on every full scheduled run since 2026-09-19; run 108 crawled 12,251 rows and stored none.

WHY THE OBVIOUS FIXES ARE WRONG. Raising the cap needs SQL access this repo does not have, and even at an infinite cap the run would still write thousands of rows a night into a queue table whose only purpose is to be read back and acked one step later. Enqueuing only dedupe-proven-new rows cannot help either: run 108's 676 rows survived the dedupe and the very first INSERT was still refused, because the rolling window was already full of yesterday's rows.

THE DECISION. Two changes, together:

1. STOP FILTERING INSIDE THE CRAWLER. app/crawl.py's _post_inbox currently runs classify_role BEFORE the insert and drops everything that is not an experienced nursing role -- 5,976 of 12,251 rows on run 108. That filter was added to survive the cap. It goes. The crawler must deliver every vacancy it finds, because we may need those rows later and re-crawling to recover them is expensive and lossy. This is the same principle as TASK-11 (raw-first: never delete, label instead of filter) and TASK-14 (Ivan, 2026-09-09: he is against our own limits on content).

2. THE INBOX MOVES TO SQLITE. The queue stops living in Postgres and lives locally instead, where there is no per-client write cap and no reason to ration rows. Processing the inbox then becomes the place where filtering, matching and conversion happen, and ONLY the resulting clean rows are loaded into Postgres -- a volume that is small and predictable by construction, always inside any limit.

Net effect: Postgres receives finished, matched, filtered postings. SQLite holds the complete raw record of everything every board ever served, available for reprocessing without re-crawling.

THINGS THE IMPLEMENTATION MUST RESOLVE, not assume:
- The Postgres inbox has OTHER producers that cannot be moved: the browser collector, POST /api/ingest and the Firecrawl webhook all hold only the anon key, which is the entire reason sql/010_inbox.sql exists. They are tens of rows a day, far under the cap. The processing step should drain BOTH queues; the Postgres one stays for them.
- Retention and reprocessing: raw rows are kept, marked processed rather than deleted, and it must be possible to re-run processing over historical raw rows after a classifier or matcher change. That is the main payoff of the whole design.
- data/app.sqlite already exists and holds crawl_runs/run_log. Decide deliberately whether the inbox belongs in that file or its own, and say why.
- Volume check: measure what actually reaches Postgres per night under the new shape, and confirm it is comfortably inside the 2000/24h window.
- The 57014 statement-timeout failure mode fired on the inbox INSERT itself and disappears with the table; confirm rather than assume.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/crawl.py writes every crawled row to the SQLite inbox unfiltered -- classify_role no longer gates what is stored, and run 108's 5,976 dropped rows would now be retained
- [x] #2 Inbox processing reads the SQLite queue, applies filtering/matching/conversion there, and writes only clean matched rows to Postgres via EdgeSink; raw rows are marked processed, never deleted
- [x] #3 The Postgres inbox remains available for the anon-key producers (browser collector, /api/ingest, Firecrawl webhook) and the processing step drains both queues
- [x] #4 Historical raw rows can be reprocessed without re-crawling, demonstrated by re-running processing over an existing run's raw rows and showing the result
- [x] #5 Measured: rows written to Postgres per full scheduled run under the new shape, shown to be comfortably inside the 2000-per-rolling-24h cap, with the 12,251-row run 108 as the worked example
- [ ] #6 A full adapter run completes intake end to end with n_new reflecting real new postings, and the 57014 timeout mode is confirmed gone rather than assumed gone
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New pflege_jobs/inbox_db.py: local SQLite queue (data/inbox.sqlite, own file -- pflege_jobs must not import app/, and a bulk fast-growing queue should not contend with app.sqlite's session/settings writes). Same row shape as pflege_jobs.inbox so one processor handles both.
2. app/crawl.py: adapter rows go to inbox_db.enqueue() unfiltered (classify_role filter deleted from _post_inbox, which stays for the anon-key producers); refs for link-cross/verify come from the rows the drain actually loaded; _unseen_source_urls also consults the local queue so the Firecrawl spend gate keeps refusing 'adapter covers it'.
3. pflege_jobs/cli.py: extract the row processing out of _drain_once; cmd_inbox drains the SQLite queue first, then the Postgres one. Local rows are acked in place (processed_at/process_note), never deleted.
4. Reprocessing: inbox_db.reset() clears processed_at for a run so the next drain re-runs classifier+matcher over historical raw rows.
5. tools/task95_replay.py: replay crawl_output/run_108.jsonl through the new path with no writes; report rows enqueued vs rows that would reach Postgres.
6. Tests, mutation-tested; targeted then full offline suite. No production writes this round.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
IMPLEMENTED 2026-09-21. No production data was written this round; everything below is measured offline or by replaying a real run.

Files changed
  pflege_jobs/inbox_db.py     NEW. The local raw queue: enqueue/pending/ack/reset/loaded_refs/known_urls/counts over data/inbox.sqlite (WAL). Same row shape as pflege_jobs.inbox so one processor handles both.
  pflege_jobs/cli.py          _drain_once split into _process_rows (the shared processing: NON_PROD_HOST gate, jobposting_to_obs, role/Bavaria filter, Matcher, EdgeSink, clinic_links) + two thin drains, one per queue. cmd_inbox pages the SQLite queue first, then the Postgres one; --reprocess-run/--reprocess-all replay stored raw rows; --inbox-db points at another queue file. Per-page log line now names the queue.
  app/crawl.py                execute() calls _enqueue_local() instead of _post_inbox(); refs for link-cross/verify come from inbox_db.loaded_refs(run_id), i.e. the rows the drain actually turned into observations. _post_inbox keeps the anon-key producers and no longer runs classify_role before the insert. _unseen_source_urls also consults the local queue (otherwise the Firecrawl 'adapter covers it' refusal could never fire again). Intake failure is recorded as a crawl_issue (TASK-92 AC#5's first half).
  pflege_jobs/sources/inbox.py  observation payload provenance carries the queue, since inbox_id is only unique within its own queue.
  app/data.py                 inbox_summary reports the local queue too -- otherwise GET /api/inbox shows an empty queue while a night's crawl waits on disk.
  crawlers/load_crawl_output.py  loads a directory of crawl output into the local queue instead of POSTing thousands of rows at the capped table.
  sql/010_inbox.sql, docs/overview.md, docs/scraping.md  scope of the Postgres table and the measured 2000-per-rolling-24h rule written down.
  tools/task95_replay.py      the no-write proof (below). tests/test_inbox_sqlite_queue.py 6 new tests; tests/test_crawl_board_retry.py + tests/test_cli_inbox_drain.py updated.

Which SQLite file, decided rather than defaulted: its own, data/inbox.sqlite, not data/app.sqlite. app/ imports pflege_jobs and never the reverse, and the queue is written by the CLI as well as by the app, so the module has to live in pflege_jobs; and this is bulk, fast-growing data (40.8 MB for one run's 8,743 rows) with its own retention story, next to app.sqlite's small operational state that a web process holds a lock on. Path override: PFLEGE_INBOX_DB or cli inbox --inbox-db.

Replay of the last real run, no writes (tools/task95_replay.py crawl_output/run_108.jsonl, full output /tmp/task95-replay-run108.txt):
  crawl_output/run_108.jsonl   8743 raw rows, 6797 distinct source_url
    (run 108's n_rows=12251 = these 8743 queue rows + 3508 seeded-adapter observations that already went straight to EdgeSink)
  OLD PATH  5976 rows dropped by classify_role before the insert; 2137 distinct urls still offered to a table that takes 2000 per client_id per rolling 24h -> refused on the first chunk, 0 stored
  NEW PATH  8743 rows queued locally (40.8 MB), 0 rows written to the capped table by the crawler at all
            processing -> 1467 observations to Postgres (16.8% of the raw rows), 1221 of them matched to a clinic
            skips: 5423 nicht_pflege, 1300 outside Bavaria, 350 ausbildung, 143 pflegehelfer, 60 werkstudent_praktikum -- every one of them still on disk with its process_note
  REPROCESS 8743 rows unmarked and run again -> 8743 read, 1467 observations, same result, no re-crawl

Volume against the cap (AC#5): the crawler now writes ZERO rows to pflege_jobs.inbox, so the 2000/rolling-24h rule is not approached from this path at all. What it does write is 1467 observations through EdgeSink (pflege-ingest), which is the same path that already carried 768 observations plus 2901 resolve-refreshes on run 96 without a cap error. The remaining Postgres inbox producers are the anon-key ones: the live queue read today holds exactly 1 unprocessed row.

Both Postgres failure modes are removed from the crawler path by construction, not by tuning: 400 P0001 and 500 57014 both fired on A.rest_post('inbox', ...) (every landed row count is an exact multiple of that call's 200-row chunk), and execute() no longer makes that call. They remain reachable for /api/ingest and the Firecrawl webhook, at tens of rows a day.

Mutation tests, each confirmed red then restored green:
  re-add a classify_role filter before enqueue        -> test_the_crawler_queues_every_row_it_found... FAILS
  ack deletes rows instead of marking them processed  -> test_processing_writes_only_clean_rows... and ...reprocessed... FAIL
  reset() becomes a no-op                             -> test_historical_raw_rows_can_be_reprocessed... FAILS
  cmd_inbox drains only the Postgres queue            -> test_cmd_inbox_drains_the_postgres_queue_too FAILS
  _unseen_source_urls ignores the local queue         -> test_the_spend_gate_sees_urls_the_adapter_already_queued_locally FAILS
  drain stops filtering by role_class                 -> two tests FAIL
  drop the intake crawl_issue                         -> test_a_failed_intake_is_recorded_as_a_crawl_issue FAILS
Targeted: 6 passed. Full offline suite: 1266 passed, 1 skipped, 0 failed (1260 passed before; 6 new tests, one existing test inverted because the behaviour it pinned is what this task removes).

Storage note, not a cap and not acted on: the raw rows are now stored twice, once as crawl_output/run_*.jsonl (386 MB so far) and once in data/inbox.sqlite (~40 MB per full run). Worth a decision on whether the JSONL archive is still wanted; nothing was deleted.

Per-criterion evidence.
AC#1 CHECKED. tests/test_inbox_sqlite_queue.py::test_the_crawler_queues_every_row_it_found_including_the_ones_intake_will_drop: execute() over a board serving 5 mixed titles stores all 5 (doctor and kitchen included); mutation-tested by re-adding the filter. Run 108's 5,976 dropped rows are retained under this shape -- the replay stores 8,743 where the old path stored at most 2,767.
AC#2 CHECKED. tests/...::test_processing_writes_only_clean_rows_to_postgres_and_keeps_the_raw_rows -- 6 queued rows, 2 observations written, 6 rows still in the table each carrying a process_note (loaded / not an experienced nursing role / non-production host). Confirmed at scale by the run 108 replay: 8,743 stored, 1,467 written.
AC#3 CHECKED at the code level, with one honest gap. cmd_inbox pages the local queue and then the Postgres queue (tests/...::test_cmd_inbox_drains_the_postgres_queue_too, mutation-tested by removing the Postgres loop), _post_inbox still serves POST /api/ingest and the Firecrawl webhook (tests/test_crawl_board_retry.py::test_post_inbox_stores_every_row_it_is_given, tests/test_agent_api.py), and the Postgres drain's ack path stays covered by tests/test_cli_inbox_probe.py. NOT done live: acking real rows is a production write, which this round may not make. The live queue read-only today holds 1 unprocessed row (inbox_id 25612, collector vendor-wp_jobs-v1), which the next real drain will take.
AC#4 CHECKED. Twice: a unit test that changes EXCLUDED_ROLE_CLASSES between two drains of the same stored rows (2 observations -> 3, no re-crawl), and the run 108 replay, which unmarks all 8,743 stored rows and reprocesses them to the same 1,467.
AC#5 CHECKED. Worked example above: 0 rows to the capped table from the crawler, 1,467 observations through EdgeSink, against a rule of 2000 inbox rows per client_id per rolling 24h that the old path hit on its first chunk.
AC#6 NOT CHECKED. It needs a full adapter run that actually writes to production, which this round is not allowed to do. The 57014 mode is removed by construction (the statement that timed out was the inbox INSERT the adapter path no longer issues), not re-observed; the next scheduled mode=adapter run at 03:00 UTC is the evidence for both halves.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:16
---
Left In Progress: AC#6 is the only open one and it needs a real scheduled run writing to production, which this round was not allowed to make. Everything else is verified offline or by replaying crawl_output/run_108.jsonl. Nothing in this round mutated production data; there is nothing pending human application except deciding whether to also keep the crawl_output/*.jsonl archive now that the same raw rows live in data/inbox.sqlite.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The crawler's queue is local SQLite and nothing filters at the queue. app/crawl.py writes every row a board serves to pflege_jobs.inbox_db (data/inbox.sqlite) unfiltered and undeduped; pflege_jobs.cli cmd_inbox is now the single processing step, draining the local queue and then the Postgres inbox (which stays for the producers holding only the anon key), and only the finished observations go to Postgres through EdgeSink. Raw rows are marked processed, never deleted, and cli inbox --reprocess-run/--reprocess-all replays them after a classifier or matcher change. classify_role no longer runs before any insert. Proven without writing to production by replaying the last real run: crawl_output/run_108.jsonl, 8,743 raw rows -- old path dropped 5,976 at crawl time and still offered 2,137 urls to a table that takes 2,000 per client_id per rolling 24h (refused on the first chunk, 0 stored); new path stores all 8,743 locally, writes 0 rows to the capped table and 1,467 observations (16.8%, 1,221 matched to a clinic) to Postgres, and reprocessing the same stored rows returns the same 1,467 with no re-crawl. Both Postgres failure modes (400 P0001, 500 57014) fired on the inbox INSERT the adapter path no longer issues, so they are gone from this path by construction. 6 new tests, each mutation-tested red then green; full offline suite 1266 passed, 1 skipped, 0 failed. AC#6 is open: it needs a real scheduled run, which this round could not make.
<!-- SECTION:FINAL_SUMMARY:END -->
