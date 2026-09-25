---
id: TASK-154
title: >-
  crawlers/load_crawl_output.py's own snapshot() queries v_postings directly,
  hits the same 57014 statement timeout TASK-124 fixed only inside
  cmd_link_cross
status: Done
assignee:
  - '@claude'
created_date: '2026-09-24 11:51'
updated_date: '2026-09-24 17:32'
labels: []
dependencies: []
ordinal: 154000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-24: running crawlers/load_crawl_output.py, the actual pipeline (inbox drain, resolve_postings, link-cross, clinic_links push) all completed successfully, but the script's own tail-end reporting crashed: snapshot()'s q('v_postings?select=posting_id&employer_class=eq.clinic&is_pflege=eq.true&status=eq.open&verify_status=eq.live&limit=100000') hit 'canceling statement due to statement timeout' (57014, the exact TASK-124 error), and since q() doesn't check the response status, the error JSON dict got iterated as if it were a list of clinic rows (TypeError: string indices must be integers, not 'str'). TASK-124 fixed this specific cost driver (v_postings' source_codes correlated subquery) only inside pflege_jobs/cli.py's cmd_link_cross (replaced with client-side source_codes_by_posting()) -- it did not touch the view itself, so any OTHER direct v_postings consumer with a large row set (this script's is_pflege=eq.true over up to 100000 rows) can still hit the same wall.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 crawlers/load_crawl_output.py's snapshot() no longer selects source_codes-bearing v_postings columns for a query this large -- either select fewer columns (posting_id alone doesn't need the view at all, postings would do) or apply the same TASK-124-style fix
- [x] #2 q() (or whatever REST helper this script uses) checks the response status and raises/prints clearly on a non-200 instead of silently treating an error object as data
- [x] #3 Live-verified: a full crawlers/load_crawl_output.py run completes without the snapshot crash
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce root cause live via SUPABASE_DB_POOLER_URL/psycopg2: EXPLAIN (ANALYZE, BUFFERS) the exact snapshot() v_postings query to see whether source_codes (TASK-124's cost driver) or something else is the bottleneck for THIS query shape.
2. Based on evidence, pick the cheapest correct-enough query: test candidates (v_postings filtered on clinic_id instead of employer_class; plain postings+role_classes join) via EXPLAIN, compare cost and row-count parity against the original.
3. Fix q() to check r.status_code and raise clearly on non-200 instead of returning r.json() unconditionally.
4. Rewrite snapshot() to use the cheapest verified query; document the root cause and the row-count trade-off in a code comment.
5. Add tests/test_load_crawl_output.py (the script has no __main__ guard so it cannot be imported directly without running its whole pipeline; extract q()/snapshot() source from the real file into an isolated exec namespace with a fake requests module) pinning both fixes.
6. Mutation-test both changes independently (revert q()'s status check; revert snapshot()'s query) via a /tmp backup, confirm red, restore byte-identical, confirm green.
7. Run the full test suite for collateral regressions.
8. Wait for the concurrently running production crawl (rhv_free_discovery, PID 2283091) to exit, then run crawlers/load_crawl_output.py for real against the live default crawl_output/ dir and confirm it completes with no snapshot crash (AC#3).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
ROOT CAUSE (live EXPLAIN ANALYZE via SUPABASE_DB_POOLER_URL/psycopg2, read-only, run against real production data while a concurrent crawl was active): TASK-124 found source_codes as v_postings' cost driver, but for THIS script's specific query shape (filters on employer_class=eq.clinic, not just selects posting_id) source_codes was ALREADY correctly pruned by the planner (never appears in the EXPLAIN output at all, confirmed) -- the actual cost driver here is DIFFERENT: v_postings.employer_class is a per-row CASE expression whose 'unknown'-downgrade branch depends on a linked_towns CTE (a GroupAggregate + Sort over every postings row with clinic_id IS NOT NULL, ~3361 rows live). Postgres only prunes that CTE when employer_class is not referenced ANYWHERE in the outer query (TASK-124's own finding, for a query that never filtered on employer_class) -- this script's query DOES filter on employer_class, so the CTE runs on every call. Measured live: original query (v_postings, employer_class=eq.clinic AND is_pflege=eq.true AND status=eq.open AND verify_status=eq.live) = 8675 buffer hits, 965ms actual execution time, 2569 rows. Rewritten query (plain postings table, clinic_id is not null AND status=eq.open AND verify_status=eq.live, joined to role_classes for is_pflege) = 1566 buffer hits, ~5-6ms, 2488 rows -- ~150x fewer buffers. Also verified as a control: v_postings filtered on clinic_id instead of employer_class gets the SAME cheap plan (1566 buffers) -- confirms the CTE, not the view per se, was the cost, and that avoiding employer_class as a filter is what matters. Chose the plain postings table (not v_postings-with-clinic_id-filter) per AC#1's own literal wording ('postings would do') and because it mirrors the existing precedent in pflege_jobs/cli.py cmd_link_clinics (comment at line 102: 'Read the base tables, not v_postings... Nothing here needs that column').

TRADE-OFF (measured, not assumed): postings.clinic_id IS NOT NULL undercounts the view's employer_class='clinic' definition by ~3% (2488 vs 2569 live rows on 2026-09-24) because the view's CASE also classifies a posting as 'clinic' when its EMPLOYER is clinic-typed even if that specific posting has no clinic_id yet (a linked_towns-driven refinement). Accepted: snapshot() only powers this script's own before/after diagnostic print (new_live count, ATS-label-changed list), not a data write or any downstream decision.

CODE (crawlers/load_crawl_output.py): q() now checks r.status_code != 200 and raises RuntimeError(f'GET {path} -> HTTP {r.status_code}: {r.text[:500]}') instead of unconditionally returning r.json() -- this is what let the original 57014 error dict get iterated as if it were posting rows (TypeError: string indices must be integers, not 'str'). snapshot()'s v query changed from v_postings?select=posting_id&employer_class=eq.clinic&is_pflege=eq.true&status=eq.open&verify_status=eq.live&limit=100000 to postings?select=posting_id,role_classes!inner(is_pflege)&clinic_id=not.is.null&status=eq.open&verify_status=eq.live&role_classes.is_pflege=eq.true&limit=100000. ats query (clinics table) was already cheap/unchanged.

TESTS (tests/test_load_crawl_output.py, new): the script has no if __name__ guard -- it runs its whole pipeline (inbox drain, subprocess pflege_jobs.cli inbox/link-cross, sleeps) at import time, so it cannot be imported directly in a test. Instead the test reads the real file's source text and execs just the q()/snapshot() definitions (everything before the 'rows = []' executable tail) into an isolated namespace with a fake requests module -- exercises the ACTUAL file text, not a reimplementation, so an edit to the real file is what flips these tests. 2 cases: (1) q() raises RuntimeError on a 500 response instead of returning the error body; (2) snapshot()'s posting query hits 'postings?...', not 'v_postings', 'employer_class', or 'source_codes', includes 'clinic_id=not.is.null', and correctly parses {posting_id, role_classes:{is_pflege}} rows into the live set.

MUTATION TESTING (saved crawlers/load_crawl_output.py to /tmp/task154_mut/load_crawl_output.py.orig first): (1) reverted q() to the original one-liner with no status check -> test_q_raises_on_non_200 went RED (AssertionError: 'q() must raise on a non-200 response'). Restored from the /tmp copy, diff -q confirmed byte-identical, both tests green again. (2) reverted snapshot()'s v query back to the original v_postings/employer_class string -> test_snapshot_queries_postings_not_v_postings went RED (postings_call.startswith(...) assertion failed, showed the v_postings URL). Restored from the /tmp copy, diff -q confirmed byte-identical, both tests green again.

TEST SWEEP: full suite (.venv, -m 'not network'): 1527 passed, 18 skipped, 2131 deselected, 0 failed, 408s -- +2 over TASK-153's same-day 1525-passed baseline (exactly the 2 new tests here), no regressions anywhere.

LIVE VERIFICATION (AC#3): before running, checked ps aux and found a concurrent production crawl already in flight (PID 2283091, 'rhv_free_discovery' via app.crawl.execute(), started 14:09, ~139 clinics per the task brief) -- waited for it to exit (confirmed via a background watcher polling ps -p 2283091) rather than running a second concurrent crawl, per the task's explicit concurrency instruction. Once it exited and ps aux showed no other crawl/pflege_jobs.cli processes running, ran the real entrypoint for real: `python crawlers/load_crawl_output.py` (no args -> default crawl_output/ dir, 483 .jsonl files, 672MB, spanning 2026-09-06 through 2026-09-24). Completed with EXIT_CODE=0, both before and after snapshot() calls succeeded with no TypeError and no 57014 anywhere in the log. Full pipeline ran for real: 23345 rows queued/drained across the sqlite local queue, resolve_postings refreshed 4683, clinic_links pushed 2960, link-cross found 0 dedup pairs. Final printed RESULT: 'new verified clinic Pflege postings: 320 (total now 1000)', 'ATS labels set/changed: 0', inbox notes breakdown (loaded 1025, skipped: nicht_pflege 645, pflegehelfer 209, ausbildung 56, outside Bavaria 54, werkstudent_praktikum 11).

OUT-OF-SCOPE FINDING, FLAGGED NOT FIXED: the live run's own 'total now 1000' output exposes a SEPARATE, PRE-EXISTING bug -- PostgREST silently caps every response at 1000 rows (confirmed live: GET .../postings?...&limit=100000 returns HTTP 200, Content-Range: 0-999/*, no error signal) regardless of the limit= query param. This affected the OLD v_postings query identically (same limit=100000), so it is not introduced by this fix and not in TASK-154's explicit ACs (only the 57014 crash + status-check were asked for) -- true live count of qualifying postings is ~2488-2569, not 1000. Flagging per the project's 'stop and ask before silently expanding scope' rule rather than fixing it here; the fix would be the same OFFSET-pagination-loop pattern pflege_jobs/cli.py's cmd_link_cross/cmd_link_clinics already use for their own paginated reads. Recommend a follow-up task if Ivan wants snapshot()'s counts to be exact rather than capped.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
q() (crawlers/load_crawl_output.py) now checks r.status_code and raises RuntimeError with the path/status/body on non-200 instead of returning the error dict as if it were row data (AC#2). snapshot()'s posting_id query switched from v_postings (filtered on employer_class=eq.clinic, which live EXPLAIN ANALYZE confirmed forces v_postings' linked_towns CTE to run -- 8675 buffers/~965ms) to the plain postings table joined to role_classes for is_pflege, filtered on clinic_id is not null (1566 buffers/~5-6ms, ~150x cheaper), mirroring the existing cmd_link_clinics 'read the base tables' precedent -- a measured ~3% undercount (2488 vs 2569 live rows) accepted since snapshot() only powers this script's own diagnostic print (AC#1). New tests/test_load_crawl_output.py execs the real file's q()/snapshot() source in isolation (the script has no import-safe entrypoint) and pins both fixes; mutation-tested both (reverted each fix independently, confirmed red, restored byte-identical from a /tmp copy, confirmed green). Full suite: 1527 passed/18 skipped/0 failed, no regressions. AC#3: waited for a concurrent production crawl (PID 2283091) to exit, then ran `python crawlers/load_crawl_output.py` for real against the live 672MB/483-file crawl_output/ dir -- completed exit 0, no snapshot crash, full pipeline executed (23345 rows drained, 2960 clinic_links pushed, resolve refreshed 4683). Flagged but left unfixed (out of AC scope): PostgREST silently caps every response at 1000 rows regardless of limit=, a pre-existing bug also present in the old query -- live run's own 'total now 1000' output exposes it; true live count is ~2488-2569. Recommend a follow-up task if exact counts matter.
<!-- SECTION:FINAL_SUMMARY:END -->
