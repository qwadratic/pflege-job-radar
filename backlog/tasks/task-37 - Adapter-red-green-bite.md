---
id: TASK-37
title: 'Adapter red-green: bite'
status: In Progress
assignee:
  - '@ivan.d.kotelnikov'
created_date: '2026-09-10 07:49'
updated_date: '2026-09-10 18:48'
labels:
  - harvester
dependencies: []
ordinal: 37000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
One adapter at a time, per Ivan's method (2026-09-10). Write the red completeness tests for every board this adapter serves, run them, fix the adapter until green (API page.total parity, sitemap cross-check, expired-CTA handling recorded), then break it on purpose and confirm the tests go red naming this adapter. Do not skip Playwright where plain HTTP fails; Firecrawl may be used as an oracle (about 3000 credits available, more on request). Every fetched page is snapshotted.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All completeness checks for bite are green on every board it serves in the live registry
- [ ] #2 Each of the four mutations turns exactly the matching check red for bite
- [x] #3 Rows returned carry description, city, dates and a browsable url wherever the source exposes them
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. RED: ran tests/test_adapter_completeness.py -k bite -m completeness against the live registry (22 boards) -- 3 failures: field_completeness on karriere.klinikum-gap.de (employmentType 0/56), declared_total_parity on jobs.arberlandkliniken.de and psych.mpg.de (harness COUNT_RX false-positive on URL-encoded %20 before Jobs/Stellenangebote text).
2. GREEN: fix tests/adapter_contract.py COUNT_RX (generic negative lookbehind, not bite-specific) to stop matching %-encoded digits; fix bite.py: derive employmentType from custom_field* free text in the _json.jobs.php fallback; replace the fixed page_num=1000 single call with walk_all_postings() that paginates via page.offset/page.total to the board's own end signal; remove the _fallback_jobposting_links limit=150 cap; fall through to same-origin fallbacks when a page's only widget mount fails to resolve a key (was: hard error); add Accept-Language header + 0.5s inter-request sleep on every sequential per-posting fetch (plain-HTTP-first politeness rule).
3. Add tests/test_completeness_bite.py: adapter-specific regression tests for the four fixes above (pagination walk, no-cap fallback links, employmentType-from-text, fallback-on-dead-mount).
4. MUTATION: run -k bite -m mutation, confirm each of the 4 mutations turns exactly its target check red naming bite.
5. Full-suite sanity: tests/test_bite.py + tests/test_completeness_bite.py + -m 'not network'.
6. Record findings: 3 zero-row bite-labelled boards (artemed-muenchen-sued.de, artemedmuenchen.de, klinik-feldafing.de) are registry mislabeling -- Playwright render_probe confirms all three actually run on smartrecruiters now, the b-ite mount present is a non-functional 'niiid' chatbot bundle with no real listing. Not a bite adapter defect; no DB write available to relabel; flagging for Oracle phase / registry correction.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RED (live, 22 boards): 3 failures -- field_completeness on karriere.klinikum-gap.de (employmentType 0/56 rows) and declared_total_parity false-positives on jobs.arberlandkliniken.de + psych.mpg.de (shared harness COUNT_RX matched digits inside a URL-encoded %20 right before 'Jobs'/'Stellenangebote', e.g. '...%20-%20Jobs%2FKarriere' read as declared=20). Fixed tests/adapter_contract.py COUNT_RX with a generic negative lookbehind (not bite-specific) -- verified no regression on dvinci's declared_total_parity.

GREEN (live re-run): 110 passed, 0 failed (was 107 passed/3 failed). bite.py fixes: derive employmentType from custom_field* free text in the _json.jobs.php (klinikum-gap-shaped) fallback; walk_all_postings() replaces the fixed page_num=1000 single call, paginating via page.offset/page.total to the board's own end signal instead of assuming one call always covers every tenant; removed _fallback_jobposting_links' limit=150 cap (no self-invented caps); crawl() now falls through to the same-origin fallbacks when a page's only widget mount fails to resolve a key (was: hard error), verified against amberg-shaped boards; added Accept-Language de-DE header + 0.5s inter-request sleep on every sequential per-posting fetch (plain-HTTP-first politeness rule), needed now that fallback link-walking has no cap.

MUTATION (-k bite): drop_description and api_self_link correctly turn field_completeness / public_url red naming bite (2/4 mutations fully confirmed). cap_first_page and skip_detail could not be driven red for EITHER bite board family (seeded:bite repr. dik-karriere.de, seeded:bite_jobs repr. klinikum-gap.de) -- root-caused, not papered over: client_read_paths()'s static page+script text scrape finds the literal jobs.b-ite.com/api/v1/postings/search endpoint string in NEITHER the loader (static.b-ite.com/jobs-api/loader-v1/api-loader-v1.min.js) NOR the customer bundle (cs-assets.b-ite.com/<customer>/jobs-api/<listing>.min.js) for any tenant checked -- the URL is assembled at runtime, invisible to plain-HTTP static scraping, so client['api_urls'] is always empty for bite and the harness's own pre-existing 'no oracle available' skip (not a change I made) correctly fires instead of a false green. Same root cause silences cap_first_page's declared-total oracle (no board prints 'N Stellen' text either). Fixed a real crash along the way: search() previously called raise_for_status() unguarded (the only fetch in bite.py without exception handling) -- a blocked/broken search endpoint crashed the whole crawl instead of returning ([], {error}) like every sibling fetch already does; walk_all_postings() now returns (rows, error) and crawl() reports the error cleanly. Also tightened tests/adapter_contract.py RecordCalls to only record successful (resp.ok) fetches, not merely attempted ones, and adapter_completeness's _no_observable_effect(skip_detail) to compare actual missing-read-path deltas instead of raw call-set equality -- both generic contract-helper fixes, re-verified with a standalone -k 'bite and read_path_coverage' run (22/22 passed, no regression) plus a live dvinci declared_total_parity spot-check.

Full non-network suite: .venv/bin/python -m pytest -q -m 'not network' -> 585 passed. Added tests/test_completeness_bite.py (7 offline regression tests: pagination walk, no-cap fallback links, employmentType-from-text, fallback-on-dead-mount, graceful search failure) plus tests/test_bite.py unchanged and green (10 tests).

Finding, not fixed here (no DB write access, out of adapter scope): 3 of the 22 bite-labelled boards return 0 rows -- artemed-muenchen-sued.de, artemedmuenchen.de, klinik-feldafing.de. Playwright render_probe confirms all three now run entirely on smartrecruiters; the only b-ite mount present is a non-functional 'niiid' chatbot bundle (ships createClient({key:''})), confirmed via crawlers/render_probe.py live evidence. This is registry ats_type mislabeling (stale bite label), not a bite.py defect -- flagging for the Oracle phase / a registry-correction task.

Re-verified 2026-09-10 (fresh subagent run): live completeness -k bite -m completeness = 110 passed/0 failed (22 boards x 5 checks, 17m25s). Mutation -k bite -m mutation = 4 passed/4 skipped (drop_description + api_self_link red naming bite on both bite/bite_jobs families; cap_first_page + skip_detail skip -- no oracle, root-caused, unchanged from prior finding). Offline: test_bite.py + test_completeness_bite.py = 17/17 passed. Full suite -m 'not network' = 655 passed/1 failed/1 skipped -- the 1 failure (test_auth.py::test_open_routes_stay_open[GET-/api/schedules]) is unrelated to bite (auth/schedules, owned by another concurrent session's changes to app/auth.py). Live per-board rows+field-completeness pulled for all 22 boards, all fields >=90% populated except the 3 known zero-row mislabeled boards (artemed-muenchen-sued.de, artemedmuenchen.de, klinik-feldafing.de -- unchanged finding, still registry mislabeling not a bite defect).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
bite adapter: all 5 completeness checks green on all 22 live boards (was 3 red). Fixed: employmentType derivation in the gap-shaped fallback, true page.offset/page.total pagination (no fixed-size assumption), removed the fallback-links cap, fallback-on-dead-widget-mount, politeness headers/sleep, and a crash-on-network-failure in search(). 2 of 4 mutations (drop_description, api_self_link) confirmed red naming bite; the other 2 (cap_first_page, skip_detail) cannot be driven red for bite -- root-caused with evidence to the harness's static-only oracle never finding bite's runtime-constructed API endpoint as literal text in any fetched script, for any tenant, so AC2 is only partially met and left for review rather than checked. Also found (not fixed, no DB access): 3 bite-labelled boards are actually smartrecruiters now (registry mislabeling), verified live via Playwright.
<!-- SECTION:FINAL_SUMMARY:END -->
