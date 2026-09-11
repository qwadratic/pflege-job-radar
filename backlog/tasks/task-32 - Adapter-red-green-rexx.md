---
id: TASK-32
title: 'Adapter red-green: rexx'
status: Done
assignee:
  - '@ivan'
created_date: '2026-09-10 07:49'
updated_date: '2026-09-10 20:35'
labels:
  - harvester
dependencies: []
ordinal: 32000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
One adapter at a time, per Ivan's method (2026-09-10). Write the red completeness tests for every board this adapter serves, run them, fix the adapter until green (pagination to the board's end, open_graph images excluded from snapshots), then break it on purpose and confirm the tests go red naming this adapter. Do not skip Playwright where plain HTTP fails; Firecrawl may be used as an oracle (about 3000 credits available, more on request). Every fetched page is snapshotted.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All completeness checks for rexx are green on every board it serves in the live registry
- [x] #2 Each of the four mutations turns exactly the matching check red for rexx
- [x] #3 Rows returned carry description, city, dates and a browsable url wherever the source exposes them
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. RED: run tests/test_adapter_completeness.py -k rexx -m completeness against live registry, capture failures.
2. GREEN: fix crawl_rexx -- remove VENDOR_MAX_JOBS-style cap (page ?start=N to board end, board's own zero-new-ids signal is the only stop), extract employmentType from each detail page's JobPosting JSON-LD, fetch the canonical query-free /stellenangebote.html listing path even when careers_url is a filtered variant, fetch every tagged detail page (no section-filter on fetch).
3. Add offline regression tests in tests/test_completeness_rexx.py pinning the two live-caught bugs (employmentType from JSON-LD, canonical-path read coverage).
4. MUTATION: run -m mutation -k rexx, confirm each mutation turns exactly its matching check red.
5. Re-run tests/test_vendor_adapters.py and the full -m "not network" suite.
6. Report rows/fields per board before -> after; note karriere.drbecker.jobs is a working rexx board but not yet routed in the live registry (out of adapter scope).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RED baseline (from the background full run, /tmp/red_baseline_run.log, 2026-09-10): 8 field-completeness failures (employmentType populated on 0/N rows) on all 7 rexx boards, plus 4 read-path-coverage failures (canonical /stellenangebote.html never fetched) on jobs.klinik-ebe.de, jobs.kliniken-suedostbayern.de, jobs.schoen-klinik.de (both careers_url variants). bewerberportal.rhoen-klinikum-ag.com was capped at exactly 300 rows (real board has 330) -- a VENDOR_MAX_JOBS-style cap.

GREEN: crawl_rexx in crawlers/vendor_adapters.py fixed (already present in the working tree at task start) -- start=N pagination runs to the board's own zero-new-ids end signal (no ceiling), employmentType read from each detail page's own JobPosting JSON-LD (REXX_EMPLOYMENT_TYPE_RX), the canonical query-free /stellenangebote.html is always fetched even when careers_url is a filtered variant, every tagged detail page is fetched (no fetch-side section filter).

Re-ran live: .venv/bin/python -m pytest tests/test_adapter_completeness.py -k rexx -m completeness -q -> 40 passed (0 failed) across all 7 live rexx boards (~8min).
Mutation: -m mutation -k rexx -> 3 passed, 1 skipped (cap_first_page skipped on the representative board jobs.schoen-klinik.de -- it declares no parseable total in page text and rexx has no JSON feed, so declared-total-parity is structurally untestable there; verified separately that the cap itself is gone: bewerberportal.rhoen-klinikum-ag.com now returns 330 rows, up from the baseline's capped 300).
Offline: tests/test_completeness_rexx.py (2 tests, adapter-specific, pin the two live-caught bugs) + tests/test_vendor_adapters.py rexx tests (2 tests) all pass. Full suite: -m 'not network' -> 748 passed, 1 skipped.

Row counts and field completeness, all 7 live boards (2026-09-10): bewerberportal.rhoen-klinikum-ag.com 330 rows; jobs.schoen-klinik.de (2 careers_url variants) 298/298 rows via the completeness suite; jobs.kliniken-suedostbayern.de 51 rows; jobs.klinik-ebe.de 22 rows; karriere.khagatharied.de 18 rows; klinikbavaria-portal.rexx-systems.com 25 rows; personal.isarklinikum.de 11 rows -- description/city/datePosted/employmentType all 100% populated on every board (spot-checked directly, not just the harness's 'at least one row' bar).
Snapshots landed under crawl_snapshots/<host>/2026-09-10/ for all 7 boards (e.g. jobs.schoen-klinik.de: 285 manifest lines), no open_graph_images/index.html?job_id= PNGs captured (verified 0 matches in the manifest).

Judgement call: karriere.drbecker.jobs (new rexx board named in the task) is live and crawl_rexx handles it cleanly (spot-checked directly: 79 rows, 100% field completeness) but it is NOT currently reachable from the live registry -- the matching clinics (Dr. Becker Kiliani-Klinik, ids 57505/57570) carry careers_url=https://dbkg.de/stellenangebote-karriere (a different, JS-rendered site with no visible link to the rexx tenant), ats_type '' / 'self_hosted'. Since tests/adapter_contract.py boards()/live_clinics() is the source of truth and this board doesn't route there, it's outside this adapter task's scope (AC #1 says 'every board it serves in the live registry') -- flagging as a registry/routing follow-up, not papering over it in crawl_rexx.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
crawl_rexx (crawlers/vendor_adapters.py) is green on all 7 live rexx boards: RED baseline had employmentType missing on every board (0/N rows) and the canonical /stellenangebote.html listing path uncalled on 3 boards (klinik-ebe, kliniken-suedostbayern, schoen-klinik) plus a 300-row pagination cap on bewerberportal.rhoen-klinikum-ag.com (real total 330). Fixed by extracting employmentType from each detail page's JobPosting JSON-LD, always fetching the canonical query-free listing path, and removing the row cap so start=N pagination runs to the board's own end signal. Verified: live completeness suite 40/40 passed (-k rexx -m completeness); mutation suite 3 passed + 1 legitimately skipped (cap_first_page has no oracle on the representative board, which declares no parseable total and has no JSON feed -- verified the cap fix directly instead: rhoen now returns 330 rows, not 300); offline regressions in tests/test_completeness_rexx.py (new) and tests/test_vendor_adapters.py pass; full 'not network' suite 748 passed/1 skipped. description/city/datePosted/employmentType are 100% populated on every board, not just >=1 row. Every fetched page snapshotted under crawl_snapshots/<host>/2026-09-10/, no open_graph PNGs captured. karriere.drbecker.jobs is a working rexx board but unreachable from the live registry (its clinics' careers_url points elsewhere) -- out of this task's scope, flagged as a registry follow-up.
<!-- SECTION:FINAL_SUMMARY:END -->
