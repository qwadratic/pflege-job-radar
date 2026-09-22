---
id: TASK-88
title: >-
  Completeness alarm: compare adapter rows against the board's own self-reported
  total on every crawl
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-21 18:50'
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
- [ ] #2 A crawl where rows < self-reported total is recorded as incomplete (a crawl_issue and a truncated-style flag), never as a plain success
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
Pre-existing mechanism found (ponytail: reuse before build) -- docs/feature-matrix.md,
app/coverage.py's FEATURE_WEIGHTS/_read_cells/_feature_score/by_adapter, and
tests/test_adapter_completeness.py's check_declared_total_parity() already implement AC#1/#4's
"ratio surfaced per board" end to end, via an INDEPENDENT oracle (raw board HTML/API text, not any
one adapter's own return value) -- see tests/adapter_contract.py:declared_total(). This is the
OFFLINE AUDIT half of the mechanism. data/feature_cells.jsonl is empty today (0 cells; the doc's own
"What is not_checked today" section says why: pytest-json-report is not installed, and
tools/cells_from_pytest.py/tests/test_adapter_completeness.py are owned by other agents/out of file
scope this round). Recorded here so nobody re-discovers this from scratch.

crawl_erecruiter worked example (AC#1's PRODUCTION-runtime half, complementary to the offline
harness above): reads TotalJobsCount + Pagination.IsPagination off the board's own embedded
`new JobList(...)` JSON, stashes session._board_total / session._board_paginated -- mirrors get()'s
existing _attempts/_ok side channel (TASK-72 AC#1) because crawl_erecruiter's return value (a plain
row list) is a hard contract the generic vendor-adapter caller in app/crawl.py depends on and this
fn cannot change. Tested: both live tenants (TotalJobsCount==len(Jobs) today) stay unaffected, and a
synthetic third-tenant case (TotalJobsCount=57, Pagination.IsPagination=true, only page 1's 1 row
returned) proves the under-read becomes visible (session._board_total=57 > len(rows)=1).

AC#2 (crawl recorded incomplete when rows < total, as a crawl_issue + non-success flag) needs
app/crawl.py's _fetch_board to read session._board_total after calling a vendor adapter and call
R.record_crawl_issue with a new kind (e.g. 'incomplete') when rows < total -- app/crawl.py is owned
by another agent this round; the signal it needs now exists on the session, wiring is the pending
piece. AC#3 (adapters with no total listed explicitly, honest 'unknown' acceptable) and AC#5
(re-run + publish the full/not-full/unknown split) both belong to the feature-matrix mechanism
above (out of file scope) or to the already-committed docs/reports/top100-coverage-audit-2026-09-21.md
section 4, which already IS that split for the 26 boards it covered live (17 proven full, 9 proven
not full via count mismatch, 2 unknown/Akamai-walled: 47601, 67601, exactly as this task's own
"preserve the honest-unknown case" instruction names) -- not re-verified today (no re-crawl this
round), cited as the standing answer until data/feature_cells.jsonl is populated for real.

Full offline suite (PFLEGE_TESTS_OFFLINE=1, matching baseline conditions): 1311 passed, 5 skipped,
0 failed, 414.63s. No regressions from the erecruiter change or the coverage.py per-feature
breakdown. (An earlier attempt without the offline gate hung ~15min into a live board fetch under
concurrent-agent network load -- a pre-existing non-termination class of issue the audit already
documents for the AMEOS board, unrelated to this diff -- killed and re-run gated for a clean signal.)

No AC checked. AC#1 is satisfied only for the one named worked-example adapter (crawl_erecruiter),
not "every adapter" as written -- the other ~18 are other agents' files. AC#2 needs app/crawl.py to
read the new session._board_total signal and is not wired (not my file this round). AC#3/#4 have
real, tested infrastructure now (app.coverage._feature_score's new 'features' per-feature-id
breakdown makes declared_total_parity visible on its own instead of blended with the other 4 feature
rows) but zero real cells exist in data/feature_cells.jsonl today, so nothing is ACTUALLY surfaced
for a real board yet -- infrastructure proven against synthetic data only, not against a real
adapter run, so left unchecked per "do not check from code presence... alone". AC#5 (re-run + publish
the split) was not re-run live this round; the already-committed top100 audit
(docs/reports/top100-coverage-audit-2026-09-21.md section 4) remains the standing answer (17 full,
9 not full, 2 honest-unknown: 47601/67601 Akamai-walled, exactly as this task's own instruction says
to preserve) until data/feature_cells.jsonl is populated for real.
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
<!-- SECTION:FINAL_SUMMARY:END -->
