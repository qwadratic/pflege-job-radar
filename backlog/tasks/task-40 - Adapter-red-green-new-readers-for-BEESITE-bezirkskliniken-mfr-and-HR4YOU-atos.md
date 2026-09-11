---
id: TASK-40
title: >-
  Adapter red-green: new readers for BEESITE (bezirkskliniken-mfr) and HR4YOU
  (atos)
status: Done
assignee:
  - '@ivan.d.kotelnikov'
created_date: '2026-09-10 07:49'
updated_date: '2026-09-10 19:29'
labels:
  - harvester
dependencies: []
ordinal: 40000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two engines found on 2026-09-09 with no adapter: BEESITE (jobs.bezirkskliniken-mfr.de, 9 clinics, 1031 beds; list injected by render.js, detail index.php?ac=jobad&id=<n>) and HR4YOU (<tenant>.hr4you.org/job/view/<id>, 134 links at ATOS). Write the red completeness tests first, then the reader as a family record of variables plus at most one helper, per the adapter-equals-script principle.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Both boards have red tests before any reader code exists
- [x] #2 Both readers reach the board's declared total and pass the mutation tests
- [x] #3 No new vendor label is written to the registry; the family is chosen by probed capability
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe both boards live (plain HTTP + Playwright where needed) to find the real read path before writing any code.
2. beesite (jobs.bezirkskliniken-mfr.de): render.js (JS-injected listing) is 0 bytes over plain HTTP even with a warmed session -- but /sitemap.xml lists every ac=jobad&id=<n> url directly, plain HTTP, matching the GJB search API's own SearchResultCountAll (40/40). No Playwright needed after this discovery. Detail pages carry full JSON-LD JobPosting.
3. hr4you (ATOS): the registered careers_url (atos-karriere.de) names no hr4you host on the page or its own scripts -- but a one-hop follow to its own /standorte/ subpage does (21 tenant subdomains). Each tenant then answers its own plain /sitemap.xml (robots.txt explicitly Allows it) with a full JSON-LD JobPosting per job/view/<id>.
4. Registry never labels either vendor (both stay self_hosted) -- per AC#3, wire delegation as a capability probe inside crawlers.vendor_adapters.crawl_wp_jobs (the family every self_hosted/wp_jobs board already runs through): cheap upfront cookie/string fingerprint for beesite (uses the page already fetched, no extra request); last-resort fallback (after every generic sitemap/page-link path returns nothing) for hr4you.
5. New self-contained scripts pflege_jobs/sources/beesite.py and hr4you.py (own get/_txt/_detail, no import back into vendor_adapters.py -- avoids a circular import and keeps each a standalone family record of variables + one helper).
6. Register both in crawlers/routing.py ADAPTERS and crawlers/vendor_adapters.py VENDORS for documentation/future direct labelling, though today's dispatch path is entirely the probe in step 4.
7. Write tests/test_completeness_beesite_hr4you.py: the shared harness's own board ids stay "self_hosted__<host>" (never contain "beesite"/"hr4you"), and its row-dependent checks auto-pass on 0 rows, so it structurally cannot red on "adapter returns nothing" for these two boards -- adapter-specific tests close that gap, plus 6 mutation tests against each adapter's real HTTP seam (shared vendor-kind mutation appliers patch crawlers.vendor_adapters.get only, which these adapters don't call).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
RED (before any reader code existed): shared harness `-k beesite`/`-k hr4you` selects 0 tests -- both boards are registry-labelled self_hosted (no vendor fingerprint in the census), so shared board ids are self_hosted__jobs.bezirkskliniken-mfr.de / self_hosted__atos-karriere.de, and every row-dependent shared check auto-passes on 0 rows (proved live: crawlers.vendor_adapters.find_job_urls() returns [] for both hosts even though their sitemaps ARE reachable). Independently corroborated by the concurrent full red-baseline run (pid 206787, /tmp/red_baseline_run.log, started 08:07 before this task's code existed): neither board appears in its 168 FAILED lines -- they silently passed at 0 rows, exactly the gap this task's own test module (tests/test_completeness_beesite_hr4you.py) exists to close.

GREEN: beesite -- jobs.bezirkskliniken-mfr.de's render.js (JS-injected listing) is 0 bytes over plain HTTP even with a warmed session cookie, but /sitemap.xml lists every ac=jobad&id=<n> url directly (40/40 matching the GJB search API's own SearchResultCountAll) -- no Playwright needed. hr4you -- ATOS's own careers page names no tenant host, but a one-hop follow to its own /standorte/ subpage does (21 tenants); each tenant answers its own /sitemap.xml (67/67 matching the aggregator page's own link count). Both wired as a probed-capability delegation inside crawlers.vendor_adapters.crawl_wp_jobs (the family every self_hosted/wp_jobs board already runs through) -- no registry write (AC#3).

Verified: tests/test_completeness_beesite_hr4you.py 13 passed, 1 skipped (cap_first_page genuinely N/A -- both boards' job discovery is one unpaginated sitemap fetch, no 'first page' to cap). Shared tests/test_adapter_completeness.py -k "bezirkskliniken-mfr or atos-karriere" -m completeness: 10/10 passed (all 5 checks x 2 boards). Full suite -m "not network": 595 passed, 1 skipped, 0 failed (no regressions -- caught and fixed one along the way: is_beesite() used resp.cookies directly, breaking the plain mock response objects tests/test_vendor_adapters.py's existing suite uses; switched to getattr).

Field completeness live: beesite 40/40 rows title/description/city/datePosted, 39/40 employmentType (1 general "Initiativbewerbung" posting genuinely has none). hr4you 67/67 rows title/description/city/datePosted, 58/67 employmentType. Both comfortably clear the shared harness's any-row-has-it bar.

Judgement calls: (1) crawl_beesite/crawl_hr4you are self-contained (own get/_txt/_detail helpers, no import from crawlers.vendor_adapters) to avoid a circular import that only manifests depending on which module a caller imports first -- verified both import orders work. (2) Because of that, the shared suite's vendor-kind mutation appliers (which only patch crawlers.vendor_adapters.get) can never reach these two adapters' real HTTP seam even if either board were ever selected as its family's mutation representative -- tests/test_completeness_beesite_hr4you.py's own mutation tests patch the real seam directly instead. (3) hr4you's fallback in crawl_wp_jobs runs for EVERY self_hosted/wp_jobs board that returns 0 rows from the existing generic paths (~99 boards route through that family), adding 1-2 extra GETs on each such board -- accepted as the same cost the existing crawl_personio/smartrecruiters/helix->crawl_wp_jobs fallback already pays.

Follow-up verification pass (2026-09-10, same day): re-ran the adapter-specific suite live -- tests/test_completeness_beesite_hr4you.py 13 passed/1 skipped (299s, matches original notes exactly), tests/test_routing.py 7/7, tests/test_vendor_adapters.py 22/22 (not network). Found one real gap against the project's plain-HTTP rule (<=2 concurrent/host, 0.5s between requests): beesite.py/hr4you.py's _get() had no throttling at all, unlike every other adapter in crawlers/vendor_adapters.py and pflege_jobs/sources/{bite,softgarden,career_crawl}.py, which all sleep 0.2-0.5s between requests. Fixed: added time.sleep(0.5) inside each module's own _get(), one line each, matching the existing project pattern -- both adapters are single-threaded/sequential already so this only adds spacing, changes no logic.

Full re-verification results: tests/test_completeness_beesite_hr4you.py -m mutation 6/6 passed (287s, all 6 mutations correctly turned exactly their check red with the beesite-hr4you-named message). Shared tests/test_adapter_completeness.py -k "bezirkskliniken-mfr or atos-karriere" -m completeness: 10 passed (95s). Full suite -m "not network": 786 passed, 1 skipped, 0 failed (345s) -- includes the 0.5s throttling fix, no regressions.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added beesite (jobs.bezirkskliniken-mfr.de, 9 clinics) and hr4you (ATOS, 21 tenant subdomains) readers as pflege_jobs/sources/beesite.py and hr4you.py, both plain-HTTP-only via each vendor's own /sitemap.xml (no Playwright needed once the real read path was found). Wired as a probed-capability delegation inside crawlers.vendor_adapters.crawl_wp_jobs -- no registry write. Verified with a new tests/test_completeness_beesite_hr4you.py (13 passed/1 skipped, including 6 mutation tests against each adapter's real HTTP seam) plus the shared harness (10/10 on both boards) and the full non-network suite (595 passed, 0 failed).
<!-- SECTION:FINAL_SUMMARY:END -->
