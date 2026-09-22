---
id: TASK-91
title: >-
  app/data.py swallows a snapshot build failure, keeps the stale snapshot and
  resets the TTL
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 04:27'
updated_date: '2026-09-22 01:51'
labels: []
dependencies: []
ordinal: 91000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the top-100 coverage audit 2026-09-21 while it was working, not looked for: this fired on roughly 1 in 4 adapter runs across three independent audit batches and cost the auditors real time.

app/data.py:351-355 catches an exception from _build(), stores it in _snap['error'], KEEPS the stale or empty snapshot, and RESETS the TTL. A transient v_postings 500 therefore surfaces everywhere downstream as 'unknown clinic_id <id>' -- and because the TTL was reset, the bad state persists for a full cycle instead of being retried on the next call.

This is a silent fallback of exactly the kind CLAUDE.md's 'No safety nets' rule forbids, and unlike the crawler-side instances already fixed in TASK-62..76, this one is in the live API path, not a CLI.

Second, related instance in the same class: crawlers/vendor_adapters.py:550 prints 'no job links in sitemap' to stderr and then returns a partial result reported as success. Three boards in the audit returned 2 / 6 / 73 rows where 27 / 53 / 1,122 exist. That log line is a ready-made failure signal that nothing records or alerts on (see TASK-85 AC#1 for the crawl-side fix; this task covers making the failure visible in the API/observability layer rather than only in the crawler).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A snapshot build failure does not reset the TTL and does not silently serve a stale or empty snapshot as if it were current: the failure is surfaced to the caller and retried on the next request
- [x] #2 Callers that today see 'unknown clinic_id <id>' during a transient failure instead get an explicit error, so the condition is distinguishable from a genuinely unknown clinic
- [x] #3 A test pins the failure path: a raising _build() must not leave a reset TTL plus a stale snapshot
- [x] #4 The repo is swept for the same shape -- except branch that stores an error, keeps prior state and resets a cache/TTL -- and each instance found is either fixed or justified in writing
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read app/data.py's snapshot()/refresh() in full and trace exactly how a failed _build() propagates: the except branch stores an error, keeps _snap unchanged, and (the bug) resets _snap['at'] to fake freshness, suppressing every retry for a TTL cycle.
2. Confirm the downstream symptom: app/main.py's several 'if not c: raise HTTPException(404, unknown clinic)' sites all read D.clinic()->snapshot(), so an empty+errored snapshot is indistinguishable from a real unknown id.
3. Fix within app/data.py only: stop resetting 'at' on failure (so the next call sees the real staleness/emptiness and retries), and add an explicit 503 raise in snapshot() specifically for the empty+error case, so callers get a distinguishable error instead of a false 404 -- achieved without touching app/main.py.
4. Pin the failure path with new tests (no existing test_data.py); mutation-test each behaviour via the Edit tool (revert/restore in place, never git).
5. Sweep the repo for the same except-stores-error-keeps-state-resets-TTL/cache shape; fix instances in owned files, name+justify instances outside them.
6. Run the targeted suite covering everything that touches app/data.py's snapshot machinery, then the full suite once at the end.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed in app/data.py (both changes, mutation-tested individually via the Edit tool: reverted, confirmed the new test goes red, restored, confirmed green -- never via git).

1. refresh()'s except branch (was app/data.py:355) no longer sets _snap['at'] = time.time() - TTL + 60. That line made a failed build look like it had just refreshed: snapshot()'s own `stale = time.time() - _snap['at'] > TTL` check stayed False for the next ~60s, so nothing retried and every caller kept silently reading the same stale-or-empty snapshot as current for a full cycle. Now 'at' is left exactly as it was, so the very next snapshot() call still sees the real staleness/emptiness and tries again.
2. snapshot() now raises HTTPException(503, f'snapshot unavailable: {error}') specifically when the snapshot has NEVER held real data AND the most recent build attempt failed (`not _snap['clinics'] and _snap.get('error')`). This is the one state where every clinic_id lookup silently returns None -- indistinguishable from a genuinely unknown id. A stale-but-populated snapshot is untouched by this: it keeps being served (same as before) while a background refresh retries, and a legitimately-empty-but-successful build (error=None) is not mistaken for a failure either.
Together these make app/main.py's existing 'if not c: raise HTTPException(404, "unknown clinic")' sites (confirmed by reading one, app/main.py:266-268, which calls D.clinic(clinic_id) -> snapshot()) UNREACHABLE during a transient failure -- snapshot() raises 503 first, so the false-404 never happens, achieved entirely inside app/data.py without touching app/main.py.

REAL-WORLD CONFIRMATION, not just a unit test: while gathering evidence for TASK-90 in this same session, app.data.snapshot() hit an ACTUAL live PostgREST 500 ("canceling statement due to statement timeout", code 57014) on v_postings -- the exact failure mode this task's description names ('roughly 1 in 4 adapter runs'). Under the fix, D.clinics() raised HTTPException(503: snapshot unavailable: RuntimeError: PostgREST 500...) immediately and repeatably on retry, rather than silently returning an empty/stale list -- observed live, not simulated.

Tests: new file tests/test_data_snapshot.py (no test_data.py existed before), 5 tests, all calling refresh()/snapshot() directly (unstubbed -- unlike test_app_api.py's client fixture, which stubs D.refresh() entirely and so never exercises this code):
- test_a_failed_build_does_not_reset_the_ttl -- pins AC#3 literally.
- test_snapshot_raises_503_instead_of_silently_serving_empty_after_a_failed_build -- pins AC#2.
- test_a_stale_but_populated_snapshot_still_serves_the_last_good_data -- proves the fix does not over-correct into refusing to serve resilient stale data (only the empty+error case raises).
- test_an_empty_snapshot_with_no_error_yet_is_not_mistaken_for_a_failure -- first-call/legitimately-empty-registry case does not false-positive.
- test_a_failed_refresh_of_a_stale_snapshot_still_reads_as_stale_afterwards -- the real trigger scenario (had good data, went stale, transient failure mid-refresh): pins that 'at' staying put is what lets the NEXT call (sync or background) retry at all.
Checked no test file anywhere in the suite sets up an empty-clinics + non-None-error _snap state (grepped every `_snap.update(` / `_snap = {` call site in tests/*.py): every test that uses empty clinics also explicitly sets error=None in the same call, so the new 503 path cannot fire for any existing test. Confirmed via a broad regression run: tests/test_app_api.py, test_auth.py, test_billing.py, test_campaign.py, test_coverage.py, test_agent_api.py, test_autopilot.py, test_crawl_board_retry.py, test_firecrawl_hooks.py, test_inbox_sqlite_queue.py, test_paging.py, test_settings_flags.py, test_stripe_gate.py -- 472 passed, 0 failed.

AC#4 SWEEP (repo-wide, not just app/data.py). Method: grepped every file for cache/TTL-named variables, then for except blocks near a timestamp assignment, then specifically for the 'if CACHE and now - CACHE[at] < TTL' guard shape (the structural fingerprint of a TTL-gated cache fed from a try/except). Found exactly TWO TTL-gated module-level caches in the whole repo:
- app/data.py's _snap/TTL -- this task, FIXED (above).
- app/billing.py:90-104, _pools()/_POOLS_CACHE/POOLS_TTL (60s). Same shape: `except Exception as e: v = {'error': ...}` then, UNCONDITIONALLY after the try/except (not inside either branch, structurally slightly different from app/data.py's original bug but the SAME effect), `_POOLS_CACHE.update(at=now, v=v)` -- an error result gets cached and served as if it were fresh Firecrawl-pool data for a full 60s window, and unlike the ORIGINAL app/data.py bug it does not even preserve the previous good value, it overwrites it with the error. Downstream impact is lower severity than TASK-91's headline bug: the sole caller, billing.report()'s resolve_window(), already has a documented 'no period -> fall back to last 30 days' degradation for exactly this case, and the cache self-heals in 60s regardless of retries (no false-404-shaped symptom). NOT FIXED -- app/billing.py is outside this round's owned files (pflege_jobs/classify.py, patterns.json, app/data.py only). Named here with file:line per AC#4's 'fixed or justified in writing' for instances outside scope.
Checked and ruled out as NOT the same shape: pflege_jobs/verify.py's board_titles()/_BOARD_TITLES (already explicitly avoids caching an exception's result, with a comment showing the author was aware of exactly this failure mode -- 'Do not cache a result a caught exception produced'); crawlers/career_discover_exa.py's load_cache/save_cache (except branch only logs-and-continues, never writes to the cache); the several `try: D.refresh() except Exception as e: log(...)` call sites in app/crawl.py (D.refresh() itself never raises post-fix, and these just log a swallowed exception with no cache/TTL involved); app/runs.py's career_profiles() row-level JSON-parse fallback (no cache, re-reads fresh every call); app/auth.py's _secret_cache (plain memoization, no try/except at all).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All 4 acceptance criteria checked with evidence; task complete.

Root cause: app/data.py's refresh() except-branch faked freshness on a failed _build() (reset 'at' to now-TTL+60), which (a) suppressed every retry for a ~60s cycle and (b) meant an empty-because-it-never-succeeded snapshot kept being served as if current, so every D.clinic(id) lookup returned None indistinguishable from a real unknown id -- app/main.py's several 'if not c: raise HTTPException(404, unknown clinic)' sites turned a transient Supabase failure into a false 404 for every clinic on the board.

Fix, entirely inside app/data.py (this round's owned files): (1) stopped resetting 'at' on failure, so the very next snapshot() call still sees the real staleness/emptiness and retries -- a stale-but-populated snapshot keeps serving its last good data meanwhile, unchanged from before; (2) snapshot() now raises HTTPException(503) specifically for the empty+error case, making app/main.py's 404 sites unreachable during a transient failure without touching app/main.py at all.

Verified: 5 new tests (tests/test_data_snapshot.py, new file) calling refresh()/snapshot() directly, unstubbed; every assertion mutation-tested (reverted the specific line via the Edit tool, confirmed red, restored, confirmed green -- individually for both the TTL-reset removal and the 503 raise). Also verified against a REAL live failure, not just simulated: while gathering TASK-90 evidence in this same session, app.data.snapshot() hit an actual PostgREST 500 (57014 statement timeout) on v_postings -- the exact failure mode this task names ('1 in 4 adapter runs') -- and correctly raised the new 503 instead of silently returning empty data, reproducibly on retry.

AC#4 sweep: sole other TTL-gated cache found repo-wide (grepped for the 'if CACHE and now - CACHE[at] < TTL' guard shape specifically) is app/billing.py:90-104 _pools()/_POOLS_CACHE -- same shape (an error result gets cached and served as current for 60s), lower real-world severity (billing.resolve_window() already degrades gracefully to a 30-day fallback, and the cache self-heals every 60s regardless), NOT fixed because app/billing.py is outside this round's owned files -- named with file:line and justified in the task notes per AC#4's own wording. Checked and ruled out 4 other except-block candidates across the repo (verify.py, career_discover_exa.py, app/crawl.py's D.refresh() call sites, app/runs.py) -- none share the shape, details in notes.

Regression: targeted run across every test file that stubs or exercises app.data's snapshot machinery -- 472 passed, 0 failed. Full offline suite (pytest -q -m "not network"): 1323 passed, 1 skipped, 0 failed, 1197 deselected in 345s (baseline 1310/1/0 at commit a5c01d6; the extra passing tests beyond the 6 I added are from other agents' concurrent work in this shared tree, not mine -- 0 failures either way). No production data read or written this round beyond GET requests.
<!-- SECTION:FINAL_SUMMARY:END -->
