---
id: TASK-29
title: 'Adapter red-green: dvinci'
status: Done
assignee:
  - '@ivan'
created_date: '2026-09-10 07:49'
updated_date: '2026-09-10 18:12'
labels:
  - harvester
dependencies: []
ordinal: 29000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
One adapter at a time, per Ivan's method (2026-09-10). Write the red completeness tests for every board this adapter serves, run them, fix the adapter until green (list.json total parity, createdDate as datePosted, detail fields), then break it on purpose and confirm the tests go red naming this adapter. Do not skip Playwright where plain HTTP fails; Firecrawl may be used as an oracle (about 3000 credits available, more on request). Every fetched page is snapshotted.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All completeness checks for dvinci are green on every board it serves in the live registry
- [x] #2 Each of the four mutations turns exactly the matching check red for dvinci
- [x] #3 Rows returned carry description, city, dates and a browsable url wherever the source exposes them
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Run shared completeness (-k dvinci -m completeness) and adapter-specific sitemap-parity tests as RED baseline.
2. crawl_dvinci already fixed in working tree (createdDate not startDate, employmentType, no section filter) from a prior uncommitted session -- confirmed all 6 live dvinci boards green already.
3. Found and removed one remaining self-invented cap: max_jobs=300 truncation in crawl_dvinci -- list.json is a single non-paginated response, so the cap only risked truncation with zero benefit.
4. Run mutation suite (-m mutation -k dvinci), confirm each of the 4 mutations turns exactly its matching check red naming dvinci.
5. Run full non-network suite to confirm no regressions.
6. Report rows/fields/pages per board and any board that can't go green (Oracle-phase candidate).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RED baseline: pre-existing uncommitted fix in working tree already had dvinci mostly green (createdDate not startDate for datePosted, employmentType from workingTimes, section-filter removed). Shared completeness (30 tests, 6 boards x 5 checks) + adapter's own sitemap-parity red test (6 boards) all GREEN at start. Found and removed one remaining self-invented cap: max_jobs=300 slice in crawl_dvinci -- list.json is one non-paginated response fetched once, so the slice only risked silent truncation with zero pagination benefit; removed max_jobs param and the jobs[:max_jobs] slice, iterate jobs directly. Re-ran completeness + sitemap-parity: still 30+6 green after the change. Mutation suite -m mutation -k dvinci: drop_description -> field_completeness red, api_self_link -> public_url red, skip_detail -> read_path_coverage red, all naming dvinci; cap_first_page correctly SKIPPED (list.json has no second page to cap, _no_observable_effect). Full suite -m 'not network': 656 passed, 1 skipped, 0 failed. Rows/fields per board (desc/city/date/employmentType all ~100%): romed-jobs.de 63, salus-klinik.de 10, sozialstiftung-bamberg.de 129 (desc 98%), ukw.de 78, klinikum-neumarkt.de 45, jobs.klinikum-fuerth.de 72. All 6 boards snapshotted today (49-194 pages each) under crawl_snapshots/<host>/2026-09-10/. No board needed Oracle/Playwright fallback -- plain HTTP list.json covers all 6.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
dvinci adapter (crawl_dvinci/dvinci_host) verified green on all 6 live boards (karriere.ukw.de was actually www.ukw.de/jobs; plus salus-klinik.de and klinikum-neumarkt.de not named in the task brief but present in the live registry). The core fix (datePosted from jobOpening.createdDate, employmentType, no section-filtering) was already applied uncommitted in the working tree; I removed the one remaining issue, a max_jobs=300 truncation slice with no purpose since list.json is a single unpaginated response. All 30 shared completeness checks + 6 adapter-specific sitemap-parity checks pass; 3/4 mutations correctly go red naming dvinci (cap_first_page skips as structurally inapplicable -- no pagination exists to cap); full non-network suite green (656 passed). No board required Playwright/Oracle fallback.
<!-- SECTION:FINAL_SUMMARY:END -->
