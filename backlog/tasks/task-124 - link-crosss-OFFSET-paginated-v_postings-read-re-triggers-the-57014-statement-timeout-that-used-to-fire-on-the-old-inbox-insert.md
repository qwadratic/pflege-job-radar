---
id: TASK-124
title: >-
  link-cross's OFFSET-paginated v_postings read re-triggers the 57014 statement
  timeout that used to fire on the old inbox insert
status: Done
assignee: []
created_date: '2026-09-23 07:55'
updated_date: '2026-09-24 00:47'
labels: []
dependencies:
  - TASK-92
priority: medium
ordinal: 124000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 pflege_jobs/cli.py's cmd_link_cross cross-source dedupe stage (the v_postings GET, currently offset-paginated at limit=1000) is confirmed to re-run pflege_jobs.v_postings' own linked_towns CTE (a GROUP BY over every postings row, sql/012_task105_requirements_fields.sql) on every page, not just the current one -- verify via EXPLAIN or a timed before/after (needs SQL access, TASK-76 AC#3's standing limitation, or a live timing comparison)
- [x] #2 A concrete fix is chosen and justified against alternatives (keyset/cursor pagination on posting_id instead of OFFSET, a larger page size to cut the number of round trips, materializing linked_towns as its own indexed table/materialized view instead of a CTE recomputed per query, or something else) -- picked with evidence, not the first idea tried
- [x] #3 Reproduced fixed: a real link-cross run against the live registry completes with no 57014, under conditions similar to today's failure (concurrent crawl load)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-23: attempted to unblock this task's own AC#1/#2 blocker (needs SQL-level access, e.g. EXPLAIN
the view) by testing the SUPABASE_DB_URL connection string in .env directly (psycopg2-binary installed
this session; was not previously available). Result: connection fails with "Network is unreachable" --
db.klkxfvieaxpjlplloljn.supabase.co resolves to an IPv6-only address (confirmed via
socket.getaddrinfo -- 3 identical AAAA results, zero A/IPv4 records), and this sandbox has no IPv6
egress. This is Supabase's well-known direct-connection-is-IPv6-only limitation, not a credentials or
permission problem -- the password itself was never tested (connection never got that far).

Fix, not guessed: Supabase's connection pooler (Supavisor) is IPv4-reachable and uses a DIFFERENT
hostname (region-specific, e.g. aws-0-<region>.pooler.supabase.com, username
postgres.klkxfvieaxpjlplloljn) -- this session does not know the project's actual pooler hostname/region
and, per this project's standing rule against inventing URLs, did not guess one. Ivan can get the exact
pooler connection string from the Supabase dashboard (Project Settings -> Database -> Connection
pooling) and drop it into .env as SUPABASE_DB_POOLER_URL (or similar) for a future session to use --
that would unblock this task's AC#1 (EXPLAIN the linked_towns CTE) and any other task gated on the same
"needs SQL access" limitation (see TASK-76 AC#3), without needing a password reset.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC#1 (this task's own hypothesis, tested and DISPROVED with evidence): live EXPLAIN (ANALYZE, BUFFERS)
via the now-working Supavisor pooler connection shows linked_towns is NOT the cost driver -- Postgres's
planner correctly prunes that whole CTE whenever employer_class/employer_id aren't in the SELECT list
(confirmed: it never appears in the plan for cmd_link_cross's actual query). The REAL cost driver is a
DIFFERENT correlated subquery in the same view: source_codes (posting_observations JOIN sources,
array_agg per row). EXPLAIN showed it evaluated once per row of the WHOLE eligible set (2240 rows,
status='open' AND clinic_id IS NOT NULL) on every single page request, BEFORE that query's own ORDER
BY/LIMIT/OFFSET are applied -- 13941 of 15256 total buffer hits, ~500ms of ~522ms execution time on a
2240-row table. This is O(pages x eligible_rows) work overall, the same blowup shape the task
suspected, just a different column causing it.

AC#2 (fix chosen against tested alternatives, not the first idea): three candidates were tried LIVE
against the real table (via BEGIN/CREATE INDEX/EXPLAIN/ROLLBACK -- never committed) before picking one:
  - Keyset pagination (posting_id > cursor) instead of OFFSET: did NOT help. The planner still chose a
    Bitmap Heap Scan (or, with bitmapscan disabled, an Index Scan) followed by a blocking Sort node,
    and the correlated subquery still ran for the full eligible set before the Sort could apply LIMIT.
  - A supporting partial index (posting_id) WHERE status='open' AND clinic_id IS NOT NULL, combined
    with keyset pagination: still no different -- even with enable_sort=off forcing a genuine ordered
    Index Scan, the Subquery Scan wrapping the view's projection (source_codes) still evaluated for
    every matching row before the outer Limit, because the view sits behind a Subquery Scan node the
    planner does not push LIMIT through.
  - Materializing linked_towns: irrelevant, per AC#1 -- it isn't the cost driver, so fixing it fixes
    nothing here (it may still be worth doing for OTHER queries that DO select employer_class, out of
    this task's scope).
CHOSEN: stop selecting source_codes from v_postings in cmd_link_cross's paginated read; compute it
client-side instead from a separate, targeted, index-backed query against posting_observations scoped
to exactly the posting_ids already paginated (chunked by 300, its own inner pagination), joined
against a fresh read of the small sources table. Confirmed live: removing source_codes from the
SELECT alone cut buffer reads 12x (15256 -> 1312) with zero rows lost. This needed no schema/index/
view change (lowest blast radius, no migration, no risk to any other consumer of the view) and
directly targets the measured bottleneck rather than the task's original guess.

Implementation: pflege_jobs/cli.py -- new pure function source_codes_by_posting(obs_rows,
source_code_by_id) (same testability pattern as this file's canonical_ref/same_source_variant_pairs/
collapse_merge_chains), wired into cmd_link_cross's stage 2. Tests: tests/test_cli_dedupe_repair.py,
3 new cases (groups distinct codes, drops an unknown source_id without fabricating one, empty input).
Mutation-tested: dropped the `if code:` guard (would insert a bare None into a posting's code set),
confirmed red on the unknown-source_id test, restored from a /tmp copy (diff -q byte-identical),
re-confirmed green.

AC#3: `python -m pflege_jobs.cli link-cross --dry-run` run for real against the live production
registry (pooler connection) -- completed in 4.5s wall time, no 57014, no errors (0 pairs found at
either stage, consistent with today's earlier TASK-99/119 cleanup already having resolved what was
outstanding). Concurrent crawl load specifically was not reproduced (no safe way to generate that from
here), but the EXPLAIN evidence shows the fix removes the O(pages x eligible_rows) scaling factor
itself, which was the structural cause independent of concurrency -- concurrency only ever made an
already-quadratic-ish query worse by adding lock/buffer contention on top.

Noted, not fixed (out of this task's scope, flagging for a decision): pflege_jobs/cli.py's cmd_verify
(the `verify` command) reads source_codes from v_postings the exact same way, with the same latent
cost shape, just not the query this task's AC's named.
<!-- SECTION:FINAL_SUMMARY:END -->
