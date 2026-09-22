---
id: TASK-88
title: >-
  Completeness alarm: compare adapter rows against the board's own self-reported
  total on every crawl
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-22 04:41'
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

2026-09-22 review-fix pass (independent Opus review of the a5c01d6 commit; fixing the one concrete defect found in this task's file scope: crawlers/vendor_adapters.py's crawl_erecruiter board-total side channel. The rework session that was supposed to fix it hit its limit before running).

Reviewer finding: session._board_total / session._board_paginated (crawl_erecruiter, previously) were written on the shared `session`, but app/crawl.py creates ONE session per whole RUN (app/crawl.py:655) and reuses it board after board -- get()'s own _attempts/_ok side channel survives that reuse only because _fetch_board explicitly resets both right before every board fetch (app/crawl.py:707); nothing equivalent existed for _board_total, so it kept the LAST eRecruiter board's number. The pending AC#2 wiring this task documented ("_fetch_board reads session._board_total after calling a vendor adapter") would, exactly as specified, have read a stale eRecruiter total for every following NON-eRecruiter board that same run and recorded a false 'incomplete' crawl_issue on a board that was actually fine.

Fixed within file scope (app/crawl.py is owned by another agent this round, so a reset "next to line 707" was not available -- used the reviewer's other named option, an adapter-scoped carrier): crawl_erecruiter now returns a _BoardTotalRows (a list subclass -- transparent to every existing caller: isinstance/len/iteration/truthiness are all identical to a plain list, preserving the hard "plain row list" return-value contract this fn cannot break) carrying .board_total/.board_paginated as instance attributes instead of session attributes. Each call gets a brand-new instance, so there is no shared mutable state left to go stale and nothing for a future caller to remember to reset.

Updated the wiring note for the still-pending AC#2 piece: the next agent that wires app/crawl.py's _fetch_board should read rows.board_total (the vendor-adapter call's own return value, right where `rows = _vendor_rows(...)` already happens at app/crawl.py:708) instead of session._board_total -- the old session-based attribute no longer exists.

tests/test_erecruiter_board_total.py's 3 existing tests updated to assert on the returned rows' own attributes instead of the session's; added a 5th test that reuses ONE shared session across two sequential crawl_erecruiter calls (board A total=57, board B total=1) and proves each call's own returned rows carry only its own board's total -- board A's return value is provably untouched after board B runs. Mutation-tested via /tmp copies (not git): reintroducing a realistic version of the original bug (writing the total onto the _BoardTotalRows CLASS instead of the per-call instance -- the same shared-state shape as the session bug) reddens exactly this new test; restored clean after.

Full offline suite after this pass: 1319 passed, 5 skipped, 0 failed, 403.64s.

AC status unchanged by this pass: 0/5 checked, for the same out-of-file-scope reasons recorded before (the other ~18 adapters and the app/crawl.py wiring belong to other agents this round). This pass is a correctness fix to the worked example's own mechanism, not new AC coverage.

2026-09-22 review-fix pass #2 (independent review of the a5c01d6 rework; the session that was meant to apply this fix hit its limit first). Reviewer finding, confirmed by re-deriving it from the live code rather than trusting the write-up: crawl_erecruiter's failed-board-fetch path (was line 1649) still returned a bare [] instead of _BoardTotalRows(), and crawl_wp_jobs' delegate loop (was lines 1174-1177) did "if rows: return rows", which is falsy for an empty-but-annotated _BoardTotalRows -- discarding the board's own total the moment a recognised eRecruiter board yielded zero rows. Live registry check (clinics?ats_type=eq.erecruiter) returns 0 rows, so every live eRecruiter board is reached ONLY through crawl_wp_jobs' delegate loop, never crawl_erecruiter directly by registry label -- meaning the discarded case (0 rows vs a declared total greater than 0) was exactly the shape this whole mechanism exists to catch, and the session-based predecessor this class replaced DID cover it (session._board_total was written before the row loop, so a 0-row read still surfaced its total). This was a coverage regression in the mechanism's most severe case, not a cosmetic gap.

Fixed both lines. crawl_erecruiter's failed-fetch path now returns _BoardTotalRows() (an empty instance; the class-level board_total=None default makes .board_total always readable, closing the AttributeError risk on that path). crawl_wp_jobs' delegate loop now reads: rows = delegate(...); if rows or getattr(rows, "board_total", None) is not None: return rows. "Not this vendor" (plain [] / board_total still None) keeps falling through to the next delegate and the generic walk exactly as before -- unchanged behavior for asklepios/concludis, which never carry this attribute. "This vendor, zero rows" (board_total set) now returns immediately instead of masquerading as an untagged empty list.

Repro, before/after, matching the reviewer's own repro shape: va.crawl_erecruiter({"name": "x", "careers_url": ""}) -- before: plain [], AttributeError on .board_total; after: _BoardTotalRows, len=0, board_total=None (readable, not missing). va.crawl_wp_jobs on a synthetic eRecruiter page with TotalJobsCount=57 and an empty Jobs array -- before: falls through past the delegate loop into the generic sitemap walk and returns a plain [] with no board_total (matches the reviewer's own "via crawl_wp_jobs: list len=0 board_total=<MISSING>"); after: returns the delegate's own _BoardTotalRows, len=0, board_total=57, without reaching the generic walk at all.

Tests added to tests/test_erecruiter_board_total.py: test_a_failed_board_fetch_still_returns_something_with_a_readable_board_total, test_crawl_wp_jobs_keeps_the_board_total_when_erecruiter_parses_zero_rows. Both mutation-tested via /tmp copies (cp crawlers/vendor_adapters.py to /tmp as a backup of the fixed file; reverted each of the two lines individually in the real file; ran the file's tests; confirmed the matching new test goes red -- AttributeError on the first mutation, AttributeError on the discarded-total path on the second, both reproducing the reviewer's exact failure shapes; restored the real file from the /tmp backup; confirmed all 7 tests green again after each restore). The 5 pre-existing tests in the file are unchanged and still pass.

Correcting the pending AC#2 handoff note this task and the prior pass recorded (grepped the whole backlog/tasks tree for "board_total" and "app/crawl.py:708" -- found this note only inside TASK-88 itself, in two places: the Implementation Plan's step 4 and the Implementation Notes' AC#2 paragraph; did not find a second task carrying it, so recording the correction here rather than guessing at another task ID): the next agent wiring app/crawl.py's _fetch_board should read getattr(rows, "board_total", None), not rows.board_total as a bare attribute access. crawl_erecruiter's own return value is now always safe to read directly (both its normal and failed-fetch paths return _BoardTotalRows), but ~18 of the ~19 other vendor adapters still return a plain list with no such attribute at all, and _fetch_board calls all of them through the same generic path -- a bare rows.board_total there would AttributeError on every one of those, and _fetch_board's own bare "except Exception" would then record that crash as a fake board failure rather than the honest "no total known for this vendor" it should be.

Full offline suite, this session's own measured run (PFLEGE_TESTS_OFFLINE=1, .venv/bin/python -m pytest -q -- .venv, not the bare "python" on PATH, is the interpreter with fastapi installed): 1340 passed, 5 skipped, 0 failed, 382.08s. Not diffed test-by-test against the harness-cited 8bf6d63 baseline (1336 passed, 1 skipped, 0 failed) or against the prior pass's own 1319 passed/5 skipped -- the extra passed/skipped counts are consistent with concurrent sibling agents' uncommitted changes to files this task does not own (git status shows crawlers/routing.py, docs/*, pflege_jobs/sources/ats_seeds.py|bite.py|career_crawl.py|pi_asp.py|softgarden.py, skill/*, web/skill/* all modified), but which specific files produced the delta was not individually verified -- stated honestly rather than claimed.

AC checkboxes unchanged: still 0/5, for the same reasons recorded in the prior pass -- the other ~18 adapters and the app/crawl.py wiring belong to other agents this round. This pass is a correctness fix to the worked example's own mechanism (closing exactly the gap the mechanism exists to catch), not new AC coverage.
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
