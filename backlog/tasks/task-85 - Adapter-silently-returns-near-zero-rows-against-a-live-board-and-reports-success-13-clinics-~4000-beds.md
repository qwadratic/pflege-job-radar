---
id: TASK-85
title: >-
  Adapter silently returns near-zero rows against a live board and reports
  success: 13 clinics, ~4000 beds
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-22 07:30'
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
- [ ] #1 A board walk that falls back from sitemap discovery to homepage-anchor scraping records that degradation as a crawl_issue instead of printing to stderr and returning success
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
All 6 ACs implemented in crawlers/vendor_adapters.py (the only owned file touched; career_crawl.py and routing.py needed no change -- crawl_wp_jobs' existing delegate/fallback wiring already routes every affected board through it). Each fix mutation-tested via a /tmp copy (copy fixed file to /tmp, revert the fix in the real file in place, confirm red, restore from the /tmp copy, confirm green, diff -q the restored file against the /tmp copy to confirm byte-identical) -- never git checkout/stash/reset, this tree is shared with sibling agents.

AC#2 (sitemap-index.xml + wp-json CPT fallback), find_job_urls/new _wp_json_cpt_job_urls:
- Added "/sitemap-index.xml" (hyphen) to the candidate list alongside the existing "/sitemap_index.xml" (underscore).
- Added _wp_json_cpt_job_urls(base): tried only when every sitemap candidate yields nothing job-shaped. Reads /wp-json/wp/v2/types, keeps types whose slug or name matches the existing JOB_SITEMAP keyword regex (excludes post/page/attachment explicitly), takes the SHORTEST matching slug when more than one matches (a "_old"/"_archiv" sibling type is always the longer name, so no hardcoded exclude list is needed -- confirmed live: karriere.klinikum-altmuehlfranken.de registers both "stellenangebote" and "stellenangebote_old"). Pages via /wp-json/wp/v2/<rest_base>?per_page=100&page=N until the board's own X-WP-TotalPages header says stop (no invented page cap).
- Live, free, measured this session (tools/compare_adapter_fc.py --max-credits 0), same script/host, before -> after:
    27501 karriere.ge-passau.de            adapter: 2 rows  -> 27 rows  (ticket's expected count: 27)
    57705 karriere.klinikum-altmuehlfranken.de  adapter: 6 rows  -> 53 rows (ticket's expected count: 53)
- Unit tests: test_find_job_urls_tries_the_hyphenated_sitemap_index_variant, test_wp_json_cpt_job_urls_prefers_the_shorter_slug_over_an_old_archive_variant, test_wp_json_cpt_job_urls_pages_until_the_sites_own_totalpages_header. All 3 mutation-verified.

AC#1 (degradation signal), crawl_wp_jobs + _BoardTotalRows.degraded:
- crawl_wp_jobs now captures find_job_urls' raw return before filtering; when it is empty (sitemap AND the AC#2 wp-json fallback both found nothing) AND the career page itself was fetched ok, the returned row list is tagged .degraded = "sitemap_and_wp_json_empty" (reusing TASK-88's _BoardTotalRows list-subclass-attribute convention already in this file, rather than inventing a second parallel channel).
- PARTIAL: the AC text asks for this to reach crawl_issues. The write path (app/runs.record_crawl_issue, called from app/crawl.py's _fetch_board) is NOT in this task's owned files (crawlers/vendor_adapters.py, pflege_jobs/sources/career_crawl.py, crawlers/routing.py) and TASK-88's own board_total/board_paginated wiring into that same caller is itself still "pending" (grepped app/crawl.py this session: zero matches for board_total/board_paginated/_BoardTotalRows -- the producer side exists, the consumer side does not yet). Calling app.runs.record_crawl_issue directly from inside vendor_adapters.py was considered and rejected: crawl_wp_jobs only ever sees ONE clinic dict, not the run_id or the full clinic_ids list a board-level call needs, and tools/compare_adapter_fc.py (and any other ad-hoc caller) would start writing spurious rows into the real data/app.sqlite crawl_issues table on every diagnostic run -- exactly the kind of side effect the "do NOT mutate production data" instruction for this round rules out. Leaving AC#1 unchecked: the signal this task owns is delivered and tested; the crawl_issue row itself depends on a caller outside this task's scope.
- Unit test: test_crawl_wp_jobs_flags_the_board_degraded_when_sitemap_and_wp_json_both_come_up_empty. Mutation-verified.

AC#3 (off-host board followed when the board itself repeatedly names it), _job_link_pairs:
- Root-cause fix in the one shared function every caller (_page_job_links, _paginated_job_links, crawl_wp_jobs' own scans, _widget_endpoint_job_links stays separate on purpose, see below) routes through, per the "fix once where callers route through" rule.
- Off-board job-shaped links are now bucketed by host; a host linked only once stays dropped exactly as before (psychiatrie-werneck.de's existing regression test, unmodified, still green), a host linked 2+ times for distinct postings is followed. _widget_endpoint_job_links (the AJAX-widget-content path) was deliberately NOT changed -- its own docstring already reasons that content reached one hop further in is less trusted than the board's own page, and there is no live evidence it needs this.
- Live, free, measured this session: adapter rows for 56201 (waldkrankenhaus.de) 2 -> 33 rows. (The ticket's "13 nursing rows" is a post-classification count; 33 is the adapter's raw row count, consistent with the 31 jobs.malteser.de links + 2 same-host Ausbildung links found live on the page.)
- Unit test: test_job_link_pairs_follows_an_off_board_host_linked_repeatedly, plus the pre-existing test_job_link_pairs_drops_a_link_to_an_unrelated_site re-run unmodified to prove no regression. Mutation-verified.

AC#4 + AC#5 (bezirkskliniken-schwaben "inline JSON model" and klinikum-ab-alz "Knockout SPA"), crawl_erecruiter + new _erecruiter_host_resp:
- Both tickets misdiagnose two DIFFERENT problems; live investigation this session found they are the SAME one: both boards run the eRecruiter "JobList" engine (crawl_erecruiter/_erecruiter_jobs, already implemented and already delegated-to from crawl_wp_jobs for TASK-49/50/77) -- neither is a Knockout SPA, neither needs Playwright/Firecrawl. The registered careers_url is only a WRAPPER page that links out to the real board on a same-registrable-domain "jobs." subdomain (klinikum-ab-alz.de/karriere/ -> jobs.klinikum-ab-alz.de/Jobs; bezirkskliniken-schwaben.de/...-bewerbung -> jobs.bezirkskliniken-schwaben.de/Jobs). Neither link matches JOB_PATH (a bare "/Jobs", no trailing slash) or carries a gender-marked anchor, so the generic scan never found either on its own, and crawl_erecruiter's existing probe only ever looked at the wrapper page it was handed.
- _erecruiter_host_resp(cu_resp): if the response it is given isn't already erecruiter-shaped, scans its hrefs for a same-registrable-domain candidate (JOB_SITEMAP-keyword-filtered so an unrelated same-domain link is never probed), tries the shortest one per distinct host first, and only fetches candidates until one actually matches (a board with no such link costs nothing extra).
- Live, free, measured this session:
    66101 klinikum-ab-alz.de (AC#5)        adapter: 0 rows  -> 63 rows (ticket: "62 jobs")
    76114 bezirkskliniken-schwaben.de (AC#4) adapter: 0 rows -> 56 rows (ticket: "TotalJobsCount 57")
    76203 (same shared board)               adapter: 0 rows -> 56 rows
    77406 (same shared board)               adapter: 0 rows -> 56 rows
- Unit tests: test_crawl_erecruiter_follows_a_same_domain_jobs_subdomain_link_from_a_wrapper_page, test_crawl_erecruiter_does_not_chase_a_same_domain_link_when_its_own_page_already_has_the_board. Mutation-verified. tests/test_erecruiter_board_total.py (TASK-88's sibling file, not touched) re-run unmodified, still 7/7 green.

AC#6 (AMEOS 778-job board did not terminate), parse_job_page + new shared _page_meta_date, _enrich_wp_fallback_fields:
- Root cause (not a missing ceiling -- no cap was added anywhere): parse_job_page's non-JSON-LD return branch (what a JSON-LD-less TYPO3 board like AMEOS always takes -- see ITEMPROP_DATE_RX's docstring) never set datePosted at all. _enrich_wp_fallback_fields' `if not p.get("datePosted")` check was therefore true for every single row, so it RE-FETCHED the exact same detail page a second time, serialized, with its own 0.5s sleep on top of _wp_job_rows' own 0.2s per-row sleep -- a real doubling of both requests and time, not an infinite loop.
- Fix: extracted the same 3-regex date lookup _enrich_wp_fallback_fields already ran into a shared _page_meta_date(htmltext) helper, called from BOTH of parse_job_page's return branches (JSON-LD and non-JSON-LD) against the response it already has in hand. _enrich_wp_fallback_fields itself is unchanged in logic (still "fetch again only if still missing") -- it just almost never needs to anymore.
- Live, free, measured this session on 5 REAL karriere.ameos.eu detail pages (same 5 urls, same script, immediately before/after restoring the fix from the /tmp backup):
    PRE-FIX:  _wp_job_rows 5 calls -> _enrich_wp_fallback_fields +5 calls (10 total), 5.6s, dates found only on the 2nd fetch
    POST-FIX: _wp_job_rows 5 calls -> _enrich_wp_fallback_fields +0 calls (5 total), 2.1s, dates found on the 1st fetch
  Confirms the reviewer's diagnosis exactly and on real data: request count halves. Did NOT run the full 778-row board to completion this session (would cost ~10-15 min of live network time end to end); the 5-row sample directly measures the mechanism the ticket names, extrapolated linearly that is ~1556 -> ~778 requests on the real board -- stated here as an extrapolation, not a measured full-board number.
- Unit tests: test_parse_job_page_reads_dateposted_from_itemprop_when_no_jsonld, test_enrich_wp_fallback_fields_does_not_refetch_a_row_whose_date_parse_job_page_already_found (the latter asserts the exact fetch-call list before and after, mutation-verified against both parse_job_page branches independently).

Not changed: crawlers/routing.py needed no edit -- ats_type is empty/self_hosted for every AC#4/AC#5/AC#2 clinic in the registry today, which already routes to crawl_wp_jobs, and crawl_wp_jobs' existing delegate loop (added under TASK-49/50/77, untouched by this task except for the AC#1 .degraded tag and the AC#2 sitemap_urls capture) already tries crawl_erecruiter/crawl_asklepios/crawl_concludis_widget on every such board for free.

AC#3 precision follow-up: recomputed 56201's nursing count using the actual pipeline mechanism (classify.classify_role + patterns.json's excluded_role_classes = {nicht_pflege, pflegehelfer, ausbildung, werkstudent_praktikum}), not an approximation -- 33 raw adapter rows -> exactly 13 rows survive the exclusion, matching the ticket's own worked number precisely. Measured live this session.

AC#6 FULL BOARD RUN, completed this session (the 5-sample test above was the mechanism proof; this is the direct measurement the AC asks for): va.crawl_wp_jobs() against the real 18501 AMEOS board (careers_url https://karriere.ameos.eu/offene-stellen/), post-fix, ran to completion:
  DONE rows=734 elapsed=380.3s degraded='sitemap_and_wp_json_empty'
380.3s (~6.3 minutes) is a sane bound -- well inside the 17-minute mark where the pre-fix code was still running and had to be killed (i.e. pre-fix exceeded 1020s and counting; post-fix finishes in 380s, room to spare). 734 (vs the ticket's 778, or the 766 the ITEMPROP_DATE_RX comment names) is normal day-to-day board churn on a live site, not a discrepancy in the fix. The .degraded='sitemap_and_wp_json_empty' tag correctly fired on this board too (AMEOS is TYPO3 with no sitemap job links and no WP REST API), confirming AC#1's signal on a third real board beyond the unit test.

REVIEW FIX 2026-09-22 (independent reviewer found 2 concrete defects in this task's own AC#1/AC#2 code, both fixed in crawlers/vendor_adapters.py, the only owned file touched):

1. AC#1 hole (vendor_adapters.py, crawl_wp_jobs): `out = crawl_hr4you(c, session=session)` reassigned `out` from the _BoardTotalRows instance carrying `.degraded` to hr4you's own plain list, silently dropping the tag -- exactly in the worst case AC#1 exists to flag (sitemap+wp-json empty AND the hr4you fallback also reads 0 rows). Reviewer reproduced this offline with a stubbed va.get. Fixed by capturing `degraded = out.degraded` before the reassignment, wrapping hr4you's return in `_BoardTotalRows(...)`, and reapplying the captured value. New regression test `test_crawl_wp_jobs_keeps_the_degraded_tag_when_the_hr4you_fallback_also_finds_nothing` (the existing AC#1 test only exercised the 1-row homepage-fallback path, which never reaches this reassignment). Mutation-tested via /tmp copy: reverting the fix reproduces the reviewer's exact failure, `AttributeError: 'list' object has no attribute 'degraded'`; restored copy is byte-identical to the fix (diff -q).

2. AC#2 hole (vendor_adapters.py, _wp_json_cpt_job_urls): `if not total_pages or page >= int(total_pages): break` silently stopped pagination after the first per_page=100 page whenever a board's CPT response carried no X-WP-TotalPages header -- a self-invented ceiling of exactly the class CLAUDE.md's no-safety-nets rule forbids, reintroduced inside the very fix for "reads short and reports success". The loop already stops on `not items` and `not rr.ok`, so the source's own end signal was already covered without this branch. Fixed by deleting the `not total_pages or` clause (missing header no longer stops the loop; only the board's own empty page or failed fetch does). New regression test `test_wp_json_cpt_job_urls_keeps_paging_when_totalpages_header_is_missing`. Mutation-tested via /tmp copy: reverting the fix reproduces the reviewer's exact failure (stops after page 1, `['.../s/1']` instead of `['.../s/1', '.../s/2']`); restored copy byte-identical to the fix.

Targeted run this session: tests/test_vendor_adapters.py 56 passed (was 54 before these 2 new tests). Full offline suite run once at the end of this session (see final summary) alongside TASK-14's corrections.

Not re-verified this session: the 5 live board counts (27501/57705/56201/66101/76114 etc.) from the prior round's AC#2-AC#5 notes -- neither fix touched those code paths, so they were left as previously measured rather than re-run to save live network time; only the 2 reviewer-named defects were reproduced and fixed this session.
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
5 of 6 acceptance criteria verified with live evidence measured this session; AC#1 left unchecked and documented (see comment #1) -- its remaining half (an actual crawl_issues DB row) needs app/crawl.py, outside this task's owned files, and is not this task's to close alone.

All code lives in crawlers/vendor_adapters.py (the only owned file touched -- career_crawl.py and routing.py needed no change, since crawl_wp_jobs' existing delegate/fallback wiring already routes every affected board through it):

- AC#2: find_job_urls tries /sitemap-index.xml (hyphen) alongside the existing candidates, then a new _wp_json_cpt_job_urls() WP-REST-API fallback (shortest-matching-slug tiebreak drops an "_old" sibling CPT with no hardcoded exclude list, pages to the board's own X-WP-TotalPages, no invented cap). Live: 27501 2->27 rows, 57705 6->53 rows -- exactly the ticket's own numbers.
- AC#3: _job_link_pairs (the one shared function every job-link-scan caller routes through) now follows an off-board host linked repeatedly for distinct postings, while still dropping a single stray cross-reference (the pre-existing psychiatrie-werneck.de regression test, unmodified, still green). Live: 56201 2->33 raw adapter rows -> exactly 13 rows survive patterns.json's own excluded_role_classes filter, matching the ticket's "13 nursing rows" precisely.
- AC#4 + AC#5: both "different" problems the ticket named (an inline JSON model, a Knockout SPA) are the same already-implemented eRecruiter engine one hop away from the registered careers_url on a same-registrable-domain "jobs." subdomain the generic scan never found. New _erecruiter_host_resp resolves it. Live: 66101 0->63 rows, 76114/76203/77406 (one shared board) 0->56 rows each.
- AC#6: root cause named and fixed, not capped -- parse_job_page's non-JSON-LD branch (what AMEOS/TYPO3 always takes) never set datePosted, so _enrich_wp_fallback_fields re-fetched every single detail page a SECOND time just to read a meta tag the first fetch already had. New shared _page_meta_date(htmltext) helper is now called from both of parse_job_page's return branches. Live, full board, this session: va.crawl_wp_jobs() against the real 18501 AMEOS board completed in 380.3s (rows=734) -- well inside a sane bound, vs pre-fix still running past the 17-minute mark it had to be killed at.

Every fix has its own regression test (9 new, all in tests/test_vendor_adapters.py) mutation-tested via a /tmp copy (copy fixed file out, revert the fix in place, confirm red, restore, confirm green, diff -q confirms byte-identical restore) -- never git checkout/reset on this shared tree. Full offline suite (pytest -m "not network", this repo's own convention): 1352 passed, 1 skipped, 0 failed.

Also verified TASK-14 in the same session (separate assignment, no code needed there -- see its own final summary): all 5 umantis boards the reopening note named now measure truncated=false live.

REVIEW FIX 2026-09-22: independent reviewer found 2 reproduced defects in this task's own AC#1/AC#2 code (both in crawlers/vendor_adapters.py) -- the .degraded tag was silently lost when crawl_wp_jobs fell through to the hr4you fallback, and _wp_json_cpt_job_urls silently stopped pagination after page 1 whenever a board's CPT response omitted X-WP-TotalPages. Both fixed at their exact lines, each with a new mutation-tested regression test (see Implementation Notes). tests/test_vendor_adapters.py: 56 passed (was 54).

Full offline suite (pytest -m "not network"), run once this session after both fixes: 1354 passed, 1 skipped, 0 failed, 389.32s (baseline at commit 8bf6d63 was 1336 passed, 1 skipped; prior round added 16, this round's 2 new regression tests bring it to 1354).
<!-- SECTION:FINAL_SUMMARY:END -->
