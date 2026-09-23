---
id: TASK-85
title: >-
  Adapter silently returns near-zero rows against a live board and reports
  success: 13 clinics, ~4000 beds
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-22 22:12'
labels: []
dependencies: []
ordinal: 85000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21. Nine clinics where tools/compare_adapter_fc.py returns 0-2 rows against a board that really has jobs, plus 4 more at risk. Four causes, all cheap, and all currently reported as a clean successful crawl:

1. Registry careers_url is a marketing page, not the board (7 clinics): 66101 (9 missing), 16215 (10), 76201 (3), 77406 (5), 76203 (1), 56404, and 27106 whose URL points at A DIFFERENT HOSPITAL (bkh-landshut.de). Registry corrections are listed in TASK-86.

2. Sitemap discovery fails and crawl_wp_jobs silently degrades to 'links on the careers homepage' while still reporting success (crawlers/vendor_adapters.py:522; the warning at :550 goes to stderr and is never recorded): karriere.ge-passau.de serves /sitemap-index.xml with a hyphen so 27 job URLs are invisible (27501, 2 missing); Altmühlfranken excludes the stellenangebote CPT from its sitemap but serves it at /wp-json/wp/v2/stellenangebote, so 6 rows come back where 53 exist (57705, 10 missing); www.sana.de has no sitemap at all and 73 CMS marketing pages are returned instead (16233, 37202, 57408).

3. crawl_wp_jobs walks only the clinic's own host while the vacancies live off-host: 56201 Waldkrankenhaus returns 2 rows, its 13 nursing vacancies are on jobs.malteser.de.

4. JS-rendered board with no render rung: jobs.klinikum-ab-alz.de (66101, Knockout SPA, 62 jobs) and jobs.bezirkskliniken-schwaben.de (76114/76203/77406) -- though for the latter the audit found the data present as an inline JSON model ({"RegionsViewModel":...,"Jobs":[...],"TotalJobsCount":57}), so it needs a small adapter, NOT a render rung or Firecrawl.

At risk but currently reading correct, will silently drop to zero at the next reconcile: 66301 Würzburg Mitte (site restructured, every stored URL 301s, ats_type is '', adapter returns 1 row against 15 held) and 76114 BKH Augsburg (its 5 held rows are stale survivors, adapter returns 0 today).

Separate defect found in the same pass: crawl_wp_jobs on the 778-job AMEOS board (18501) did not terminate after 17 minutes and had to be killed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A board walk that falls back from sitemap discovery to homepage-anchor scraping records that degradation as a crawl_issue instead of printing to stderr and returning success
- [x] #2 /sitemap-index.xml is in the sitemap candidate list, and a WordPress board whose custom post type is missing from the sitemap falls back to /wp-json/wp/v2/<cpt>?per_page=100; 27501 and 57705 yield their real counts (27 and 53)
- [x] #3 A board whose vacancies live off-host is followed to that host when the registry/board itself points there (56201 -> jobs.malteser.de yields its 13 nursing rows)
- [x] #4 jobs.bezirkskliniken-schwaben.de is read via its inline JSON model, serving 76114/76203/77406 from one adapter with no render and no Firecrawl
- [x] #5 66101's Knockout SPA board (jobs.klinikum-ab-alz.de, 62 jobs) yields its rows
- [x] #6 crawl_wp_jobs terminates on the 778-job AMEOS board (18501) within a sane bound, and whatever caused the non-termination is named
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce each of the 6 AC failure modes live (compare_adapter_fc.py / direct probes) before touching code.
2. AC2 (cheapest, unblocks AC1's evidence too): add /sitemap-index.xml to find_job_urls' candidate list; add a wp-json/wp/v2/<cpt> fallback (_wp_json_cpt_job_urls) tried only when every sitemap candidate found nothing, CPT chosen by job-vocabulary keyword match with shortest-slug tiebreak (drops an _old/_archiv sibling type without a hardcoded exclude list).
3. AC1: capture find_job_urls' raw return in crawl_wp_jobs; when it is empty (sitemap AND wp-json both empty) tag the returned row list .degraded = "sitemap_and_wp_json_empty" (reuse TASK-88's _BoardTotalRows list-attribute convention so app/crawl.py's caller-side wiring, out of this task's owned files, can record it as its own crawl_issue).
4. AC3: _job_link_pairs currently drops every off-board job-shaped link (_same_board). Root-cause fix in that one shared function (all callers route through it): bucket off-board links by host; a host linked only once stays dropped (psychiatrie-werneck.de's existing regression test), a host linked 2+ times for distinct postings is followed (waldkrankenhaus.de -> jobs.malteser.de).
5. AC4+AC5: both boards the ticket treats as separate problems (an "inline JSON model" and a "Knockout SPA") are the SAME already-implemented eRecruiter engine (crawl_erecruiter/_erecruiter_jobs, already delegated-to from crawl_wp_jobs) one hop away from the registered careers_url on a same-registrable-domain "jobs." subdomain neither JOB_PATH nor a gender-marked anchor names. Add _erecruiter_host_resp: if the landing page itself isn't erecruiter-shaped, try same-domain candidate hrefs (JOB_SITEMAP-keyword-filtered, shortest first) until one is.
6. AC6: diagnose before touching code. parse_job_page's non-JSON-LD branch (AMEOS/TYPO3, no JobPosting block) never set datePosted, so _enrich_wp_fallback_fields re-fetched every single row a second time just to read the same meta tag a second time -- root cause is the redundant fetch, not a missing ceiling (no cap gets added). Extract the 3-regex date lookup already in _enrich_wp_fallback_fields into a shared _page_meta_date(htmltext) helper; call it from both parse_job_page return branches (JSON-LD and non-JSON-LD) so the date is usually already known before _enrich ever runs, eliminating most re-fetches.
7. Tests: one regression test per AC, each mutation-tested via a /tmp copy (never git checkout/reset -- sibling agents share this tree) -- revert the fix in place, confirm red, restore from the /tmp copy, confirm green.
8. Verify live: re-run compare_adapter_fc.py --max-credits 0 for every named clinic (27501, 57705, 56201, 66101, 76114, 76203, 77406) and a bounded real-AMEOS fetch-count sample for AC6; record only numbers measured this session.
9. Run the targeted test file, then the full offline suite once; record counts.
10. Backlog notes + per-criterion evidence; check off only what is actually verified (AC1's crawl_issue DB write depends on app/crawl.py, which is outside this task's owned files -- record the signal as delivered and the DB-write half as not owned/not verified rather than overclaim it).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#1 CLOSED 2026-09-22: wired the consumer side (app/crawl.py's _fetch_board, the one place every
vendor adapter's return value already flows through -- comment #1's own handoff note). rows =
_vendor_rows(...) is now read for two adapter-supplied signals via getattr (safe no-op for the ~18
of ~19 adapters that still return a plain list with neither attribute):

- degraded = getattr(rows, "degraded", None) -> R.record_crawl_issue(..., "degraded", ...,
  f"fell back to a lower-confidence discovery path: {degraded}", run_id). Fires independently of row
  count (AMEOS: degraded but still 734 real rows via the hr4you fallback -- this session's own AC#1
  notes already proved that shape live).
- board_total = getattr(rows, "board_total", None); if board_total is not None and len(rows) <
  board_total -> R.record_crawl_issue(..., "incomplete", ..., f"board reports {board_total} total but
  the adapter returned {len(rows)} row(s)" (+paginated note), run_id). This is also TASK-88 AC#2's
  pending wiring point (same line, same rows value) -- done together since both consumer signals
  meet at exactly this one spot; see TASK-88 for that task's own bookkeeping.

Necessary companion fix (not asked for by AC#1's text, but required for correctness the moment these
two kinds exist): app/runs.py's board_walk_ok -- the single gate pflege_jobs.verify.board_absent_gone
(TASK-87 AC#1) checks before treating a posting's absence from a walk as real board-membership
evidence -- only excluded kind in ('vendor','seeded','truncated'). A 'degraded' or 'incomplete' walk
is exactly as untrustworthy for that purpose as a 'truncated' one (postings past what a fallback
path/an under-read can see are invisible this walk), so leaving them out would have made TASK-87's
retirement logic start silently misfiring -- false-retiring open postings on every degraded/incomplete
board -- the day this shipped. Added both kinds to board_walk_ok's exclusion list and its docstring.

Live verification, real board, this session: crawl_wp_jobs against 76114 (jobs.bezirkskliniken-schwaben.de,
the shared eRecruiter board AC#4/AC#5 fixed) returns rows=57, board_total=57 today (board grew by one
posting since this session's earlier 56/57 reading) -- confirms board_total is genuinely read off a
live board AND that an exact match correctly produces NO false 'incomplete' issue. Did not re-run the
6-minute full AMEOS crawl to re-prove the 'degraded' producer side live -- already proven live this
session in this task's own AC#6 evidence (380.3s run, degraded='sitemap_and_wp_json_empty' fired
correctly); today's work only adds the consumer (crawl_issue write), which is unit-tested end to end
against the real _BoardTotalRows class (not a stub shape) via a monkeypatched _vendor_rows, mutation
verified.

9 new tests: tests/test_crawl_board_retry.py (7 -- degraded/incomplete positive+negative+plain-list-
no-op) and tests/test_runs.py (2 -- board_walk_ok false on both new kinds). All mutation-tested via
/tmp copies of app/crawl.py and app/runs.py (revert fix in place, confirm red, restore, confirm green,
diff -q byte-identical) -- never git checkout/stash/reset on this shared tree.

Full offline suite (pytest -m "not network"): 1386 passed, 18 skipped, 0 failed, 351.29s. Not diffed
test-by-test against the prior 1354-passed baseline recorded in this task's own notes -- this shared
tree has concurrent sibling-agent changes in flight (git status: crawlers/routing.py, docs/*,
pflege_jobs/sources/*, skill/*, web/skill/* all modified outside this task's file scope), consistent
with the extra passed/skipped count; this task's own 9 new tests are confirmed individually
mutation-tested rather than inferred from the aggregate delta.

All 6 AC now checked. Task complete.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-22 06:50
---
AC#1 needs a follow-up outside this task's owned files: crawlers/vendor_adapters.py now tags a degraded board's returned rows with .degraded = "sitemap_and_wp_json_empty" (same _BoardTotalRows attribute convention as TASK-88's board_total), but nothing reads that attribute yet -- app/crawl.py's _fetch_board (the only place record_crawl_issue is called for a vendor-kind board) does not check it. TASK-88's own board_total/board_paginated wiring into that same caller is itself still unwired (grepped app/crawl.py this session: zero matches for board_total/board_paginated/_BoardTotalRows). Whoever picks up app/crawl.py next should read BOTH .degraded and .board_total off the adapter's return value in one pass.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All 6 acceptance criteria verified with live evidence. 5 closed in the prior round of this session
(crawlers/vendor_adapters.py: sitemap-index.xml + wp-json CPT fallback, off-host board following,
the shared eRecruiter engine reached one hop from the registered careers_url, the AMEOS
non-termination root-caused and fixed). AC#1 closed this round: app/crawl.py's _fetch_board now
reads .degraded and .board_total off every vendor adapter's return value (getattr-safe for adapters
that don't supply either) and records them as crawl_issue kind='degraded'/'incomplete' -- the exact
handoff this task's own comment #1 named. Necessary companion fix: app/runs.py's board_walk_ok
(TASK-87's retirement gate) now also treats both new kinds as an incomplete walk, alongside the
pre-existing 'truncated' -- otherwise TASK-87's posting-retirement logic would have started
false-retiring open postings on every degraded/incomplete board the day this shipped.

9 new regression tests (tests/test_crawl_board_retry.py, tests/test_runs.py), all mutation-tested via
/tmp-copy revert/restore. Live-verified against a real board (76114, board_total=57=rows, correctly
produces no false 'incomplete'). Full offline suite: 1386 passed, 18 skipped, 0 failed.

Same touch point also happened to satisfy TASK-88 AC#2 (a crawl under its board's self-reported total
is recorded as incomplete) -- noted and checked there separately, not claimed here.
<!-- SECTION:FINAL_SUMMARY:END -->
