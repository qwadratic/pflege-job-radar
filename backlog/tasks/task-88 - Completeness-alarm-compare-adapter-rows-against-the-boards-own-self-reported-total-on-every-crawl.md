---
id: TASK-88
title: >-
  Completeness alarm: compare adapter rows against the board's own self-reported
  total on every crawl
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-22 22:12'
labels: []
dependencies: []
ordinal: 88000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, section 7. This is the cheapest possible answer to 'are we parsing this source in full', and it needs no oracle, no Firecrawl and no credits.

Most boards publish their own total: page.total (b-ite), numberOfItems (softgarden feed), TotalJobsCount (Duet/bezirkskliniken), X-WP-Total (WordPress REST), '44 Treffer' (BEESITE), 'Derzeit gibt es 57 offene Stellen' (several CMS boards), and rexx/oracle paginated counts. Where the audit compared adapter output against that number, the match was EXACT 17 times and a hard failure 9 times -- so the signal is both available and discriminating.

Proven full at audit time: Roth 9/9, Kliniken Südostbayern rexx 51/51, Schön Klinik 292/292, BG Murnau 377/377, Asklepios 1398/1398, mein-check-in Landshut 22/22, Amberg 47/47, Straubing 27/27, Neumarkt 44/44, Traunstein 51/51, BEESITE Ansbach 44/44, Bayreuth softgarden 114/114, Deggendorf 28/28, Landsberg 22/22, Passau 17/17, Coburg 78/78, Dachau 33/33.

Proven NOT full: 66101 (0 vs 62), 66301 (1 vs 51), 76201 (2 vs 16), 77406/76114/76203 (0 vs 57), 56201 (2 vs 31), 27501 (2 vs 27), 57705 (6 vs 53), 16233/37202/57408 (73/85/88 CMS pages vs an Oracle board of 1,122), 16201/16203 (walk aborts on the first posting page).

Unknown and honestly so: 47601 and 67601 -- helios-gesundheit.de returns Akamai 'Access Denied' to plain curl AND to a real Chromium render from this IP, exactly as crawlers/routing.py:90 WALLED documents. No Helios count can be trusted without non-datacenter egress or a Firecrawl rung.

This task is the mechanism that makes 'we parse every source in full' a continuously-verified claim rather than a one-off audit.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every adapter that can read its board's self-reported total does so and records it alongside the row count it returned
- [x] #2 A crawl where rows < self-reported total is recorded as incomplete (a crawl_issue and a truncated-style flag), never as a plain success
- [ ] #3 Adapters whose boards publish no total are listed explicitly, with what alternative completeness evidence each one can offer -- an honest 'unknown' is acceptable, a silent assumption of completeness is not
- [ ] #4 The ratio is surfaced per board where coverage is judged, so a board that starts under-reading is visible the same day rather than at the next audit
- [ ] #5 Re-run across all boards and publish the full/not-full/unknown split as the standing answer to 'do we parse every source in full'
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Owned files only: pflege_jobs/verify.py, app/coverage.py, app/runs.py, and ONE adapter (crawl_erecruiter in crawlers/vendor_adapters.py) as the worked example -- every other adapter (b-ite, softgarden, WordPress, BEESITE, rexx/oracle) is owned by other concurrent agents.
2. Before writing anything: check for an existing mechanism (ponytail -- reuse before build). Found one: docs/feature-matrix.md + app/coverage.py's own FEATURE_WEIGHTS/_read_cells/_feature_score already wire a 'declared_total_parity' feature-matrix column end to end; tests/test_adapter_completeness.py + tools/cells_from_pytest.py already compute it per board independently of any one adapter's own return value (oracle-derived, not adapter-derived). data/feature_cells.jsonl is currently empty (0 cells) and the doc names why: pytest-json-report is not installed. This is the OFFLINE AUDIT half of AC#4/5 and is not mine to populate (tools/cells_from_pytest.py and tests/test_adapter_completeness.py are out of file scope).
3. crawl_erecruiter worked example: read TotalJobsCount + Pagination.IsPagination off the board's own embedded JSON, stash on the shared `session` (mirrors get()'s existing _attempts/_ok side channel, TASK-72 AC#1) since the fn's return shape (a plain row list) is a hard contract the generic vendor-adapter caller in app/crawl.py depends on and cannot change. This is the PRODUCTION-runtime signal, complementary to (not a duplicate of) the offline pytest harness.
4. AC#2 (crawl recorded incomplete when rows < total) needs app/crawl.py to read the new session attribute and call R.record_crawl_issue with a new kind -- app/crawl.py is not mine; report as pending, wiring point identified precisely.
5. Mutation-test the erecruiter change, run targeted + full suite once, report the full/not-full/unknown split already proven by the (already-committed) top100 audit as the concrete answer to AC#5 pending a real pytest-driven re-run.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#2 CLOSED 2026-09-22, via TASK-85's AC#1 wiring pass (same file, same line -- both consumer signals
meet at app/crawl.py's _fetch_board, right where `rows = _vendor_rows(...)` already runs): rows <
board_total now writes R.record_crawl_issue(url, day, "incomplete", ..., f"board reports {total} total
but the adapter returned {len(rows)} row(s)", run_id) -- getattr-safe no-op for adapters with no
board_total (still ~18 of ~19). app/runs.py's board_walk_ok (TASK-87's retirement gate) now also
excludes kind='incomplete' (and 'degraded', TASK-85's own signal) from "walk read the board in full"
-- the "truncated-style flag" AC#2's text asks for, reusing the exact same mechanism 'truncated'
already uses rather than inventing a second one.

Live-verified real board (76114, jobs.bezirkskliniken-schwaben.de): board_total=57, rows=57 today --
exact match, correctly produces no crawl_issue. Positive case (rows < board_total) mutation-tested via
tests/test_crawl_board_retry.py's test_an_under_read_vendor_board_is_recorded_as_crawl_issue_kind_incomplete,
against the real VA._BoardTotalRows class.

AC#1 still NOT checked -- this pass wires the CONSUMER for whichever adapter supplies board_total; it
does not add board_total-reading to any more adapters (still only crawl_erecruiter, directly or via
crawl_wp_jobs' delegate loop, per this task's own prior notes). AC#3/#4/#5 unchanged from prior
rounds -- still pending the feature-matrix/data/feature_cells.jsonl population this task's own notes
already documented as out of scope this round.

Full offline suite (pytest -m "not network"), same run as TASK-85's own AC#1 close: 1386 passed,
18 skipped, 0 failed, 351.29s.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Found the mechanism this task asks for already exists in a more general form (docs/feature-matrix.md
+ app.coverage's feature-matrix wiring + tests/test_adapter_completeness.py's independent-oracle
declared_total_parity check across every adapter, not just erecruiter) -- unpopulated
(data/feature_cells.jsonl is empty; the doc's own text says pytest-json-report is not installed).
Delivered the two pieces inside file scope: crawl_erecruiter now reads and reports its board's own
TotalJobsCount/Pagination.IsPagination (the named worked example, tested against both live tenants'
real shape and a synthetic under-reading third tenant), and app.coverage._feature_score now breaks
verdicts down per feature_id so declared_total_parity stops being blended with the other 4 rows once
real cells exist. Neither AC fully holds yet at the "every adapter" / "surfaced for a real board"
level the acceptance criteria ask for -- the remaining ~18 adapters and the app/crawl.py wiring that
would make AC#2 a live guarantee belong to other concurrent agents' files this round. 0/5 AC checked;
all evidence and the precise pending wiring points are in the implementation notes. Full offline
suite: 1311 passed, 5 skipped, 0 failed.

2026-09-22 rework: fixed the reviewer-found defect in file scope (crawl_erecruiter stashed its board total on the shared per-RUN session with no per-board reset available in-scope, so a stale total from one eRecruiter board would misattribute to the next non-eRecruiter board once app/crawl.py's pending wiring reads it). Replaced with a per-call list-subclass return-value carrier (_BoardTotalRows) that needs no reset by construction. Mutation-tested via /tmp copies. Full offline suite: 1319 passed, 5 skipped, 0 failed. AC checkboxes unchanged (still 0/5 -- this pass corrected the worked example's mechanism, it did not extend coverage to other adapters or wire app/crawl.py, both owned by other agents this round).

2026-09-22 review-fix pass #2: fixed the reviewer-found regression in the worked example's own mechanism. crawl_erecruiter's failed-fetch path returned a bare [] (AttributeError risk on .board_total) and crawl_wp_jobs' delegate loop's "if rows:" discarded an empty-but-annotated _BoardTotalRows -- so the one path production actually uses (the live registry has 0 clinics labelled ats_type=erecruiter, so every eRecruiter board is reached via crawl_wp_jobs' delegate loop, never the registry label) could not see the mechanism's most severe case: 0 rows parsed against a declared total greater than 0. Fixed both lines: crawl_erecruiter now returns _BoardTotalRows() on a failed fetch; the delegate loop now also returns when board_total is not None, not only when rows is non-empty. Added and mutation-tested 2 tests reproducing the exact before/after shapes (both go red on the pre-fix code with the same AttributeError / discarded-total failure the reviewer described). Corrected the pending app/crawl.py handoff note to read getattr(rows, "board_total", None) rather than a bare attribute access, since ~18 of the ~19 other adapters return a plain list. Full offline suite this session: 1340 passed, 5 skipped, 0 failed. AC checkboxes unchanged (0/5) -- still a worked-example-only fix, not new task coverage; the other adapters and the app/crawl.py wiring remain other agents' files this round.
<!-- SECTION:FINAL_SUMMARY:END -->
