---
id: TASK-50
title: >-
  SPA/bot-walled career boards -- Asklepios (1035 beds), dbkg.de, Rotkreuzklinik
  Würzburg
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 10:49'
updated_date: '2026-09-23 09:21'
labels: []
dependencies: []
ordinal: 50000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-11 recon: three zero-yield boards look structurally different from the rest -- Asklepios (asklepios.com) renders as a client-side app shell (<div id="app"/root/...>) with no static job content; dbkg.de is the same shape; rotkreuzklinik-wuerzburg.de returns HTTP 403 outright to a plain requests fetch (same bot-wall pattern already documented for helios-gesundheit.de in crawlers/routing.py's WALLED regex). Asklepios alone is 1035 beds -- likely the single highest-value unfixed board in the registry. These need either a Playwright-based fetch, a different User-Agent/header strategy for the 403 case, or Firecrawl as the fallback (see TASK-41, Firecrawl as completeness oracle).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Asklepios board's real job API/widget endpoint identified and either wired into a new adapter or routed to Firecrawl
- [x] #2 rotkreuzklinik-wuerzburg.de's 403 root-caused: confirm whether a different UA/header unblocks it, or add it to routing.py's WALLED set like helios
- [x] #3 dbkg.de triaged the same way as Asklepios
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. asklepios.com (1035 beds, 7 clinics): the Next.js career portal posts to its own /api/search with a restEndpoint id that sits in the careers page's own HTML. Plain requests reproduces it (1398 postings, 60/page, server's own count is the stop). Add a vendor adapter that reads the restEndpoint off the careers page and pages the API; probe+delegate from crawl_wp_jobs, no registry change.
2. dbkg.de: careers page hands off to drbecker.jobs -> karriere.drbecker.jobs, a plain server-rendered board crawl_wp_jobs already reads (77 rows, 18 in Bad Windsheim). Registry careers_url fix only.
3. rotkreuzklinik-wuerzburg.de: establish whether the 403 is a datacenter bot wall (-> routing.WALLED) or a removed page. Homepage 200s from the same IP with 4 different UAs incl. curl while /stellenangebote/ 403s, and decision-1 records the clinic as insolvent/closed since 2026-04-01 -- report with evidence rather than adding a wall entry that would be wrong.
4. Tests + full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-21 live triage + fix.

Asklepios (7 clinics, 1035 beds) -- AC#1 done. The Next.js portal renders no job markup, but its own client does a same-origin JSON POST to https://www.asklepios.com/api/search, and the per-tenant search id it posts ('restEndpoint': /.rest/search/job/de/<uuid>) sits in the careers page's own HTML. Reproduced with plain requests, no browser, no Firecrawl. The server clamps its page size to 60 regardless of the requested l, so paging is driven by its own 'count' and by ids running out -- no ceiling of ours. New crawl_asklepios in crawlers/vendor_adapters.py, reached by probe-and-delegate from crawl_wp_jobs, so the 7 clinics' existing registry rows need no change. The list response already carries title, company, location, PLZ, workarea, working time, publication date and the public karriere.asklepios.com job link, so there is no per-posting detail fetch at all. Verified live: crawl_wp_jobs on the registry careers_url returns 1398 rows (collector vendor-asklepios-v1), 90 of them at the 7 Bavarian Asklepios sites, 34 of those experienced-nursing class. The board's own workareas value ('Pflege- und Funktionsdienst') is threaded through as section_labels, i.e. the nursing_section_confirmed signal.
Volume note for the operator: this is a nationwide board -- 515 of the 1398 pass _post_inbox's role filter, only 34 of them Bavarian. _post_inbox dedupes against the existing inbox by source_url, so that is a one-time backfill and steady-state writes are the daily delta. No Bavaria filter was added inside the adapter: that would be exactly the invented narrowing the project rules ban, and a board-level geo gate (if wanted) belongs in app/crawl.py applied uniformly to every nationwide board.

rotkreuzklinik-wuerzburg.de (66303, 100 beds) -- AC#2 done; the answer is 'neither UA nor WALLED'. /stellenangebote/ answers 403 to every User-Agent tried (the repo UA, Windows Chrome, Googlebot, curl) on both the apex and www hosts -- but https://rotkreuzklinik-wuerzburg.de/ itself answers 200 to all four from the same IP, and its homepage links no careers/jobs/Stellen page at all. So this is not a datacenter bot wall (a wall would refuse the homepage too, the way helios-gesundheit.de does); it is one removed page. That matches backlog decision-1: the clinic filed Schutzschirmverfahren in Sept 2025 and ceased operations 2026-04-01, ahead of the Krankenhausplan. Deliberately NOT added to routing.WALLED: that set means 'known-unfetchable, a 0 here is not a real 0', and recording a wall that does not exist would hide a closed clinic behind a transport excuse. Nothing to recover here.

dbkg.de (57505 + 57570, 40 beds) -- AC#3 done, and it is a stale careers_url, not an SPA. dbkg.de/stellenangebote-karriere hands off to drbecker.jobs, whose 'Stellenangebote' link is https://karriere.drbecker.jobs/ -- a plain server-rendered rexx-shaped board (-de-j<id>.html). No code change needed: crawl_wp_jobs already reads it. Verified live: 77 rows, 18 of them located in Bad Windsheim (the Bavarian site), 2 of those experienced-nursing class.

2026-09-22 status audit (read-only; no source/data files touched; run 118 live throughout). Fresh live re-verification: crawl_asklepios still defined in crawlers/vendor_adapters.py:1702 and wired as a probe-and-delegate target from crawl_wp_jobs (line 1286) and in the vendor dispatch map; ran tools/compare_adapter_fc.py 18811 --max-credits 0 (Asklepios Lungenklinik Gauting, Bavarian, Accept-Profile read only) -- adapter: 1400 rows today (was 1398 on 2026-09-21), using the clinic's CURRENT unmodified DB careers_url/ats_type, so AC#1's fix is confirmed already live in production, no registry dependency. rotkreuzklinik-wuerzburg.de re-fetched just now: homepage 200, /stellenangebote/ 403 -- same page-specific pattern as recorded; decision-1 (status=deferred) still exists and records the Schutzschirmverfahren/closure evidence exactly as this task's AC#2 cites. karriere.drbecker.jobs re-fetched just now: 200, page text still contains 'Bad Windsheim'/'Kiliani'/'Pflegefachkraft'. Targeted tests (same 5 files as TASK-49): 103 passed, 0 failed. Live pflege_jobs.clinics for 57505/57570 (dbkg.de) still carries the STALE careers_url=https://dbkg.de/stellenangebote-karriere -- AC#3's correction has not reached production. Decision: all 3 ACs hold under fresh evidence; NOT moved to Done -- 2 of 3 boards (Asklepios, the 1035-bed headline item, and Rotkreuzklinik) are fully resolved with no outstanding delivery gap, but dbkg.de's registry write is still undelivered today, matching (and reconfirming) the task's own final-summary reasoning rather than the earlier passing AC text alone.

2026-09-23: dbkg.de's pending registry write (57505, 57570 -> careers_url='https://karriere.drbecker.jobs/') applied live via EdgeSink.write_clinics (this session has write access, unlike the prior blocked round). Verified: both clinics now carry the corrected careers_url in production; live re-crawl returns 75 rows (was 0 on the stale dbkg.de/stellenangebote-karriere redirect page). Asklepios and Rotkreuzklinik Wuerzburg needed no registry change (already confirmed live in the prior round). All 3 boards' delivery gap is now closed.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 03:11
---
Registry write still pending (no production DB write permitted this session); applied in data/registry/clinics.csv only. Needed against pflege_jobs.clinics:
  careers_url='https://karriere.drbecker.jobs/' for clinic_id in (57505, 57570)
Asklepios needs no registry change at all. Rotkreuzklinik Wuerzburg (66303) needs no crawler change -- it needs the closure decision in backlog/decisions/decision-1 applied, or it will keep being reported as a failing board forever.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
dbkg.de's registry correction applied and verified live (57505/57570 -> careers_url='https://karriere.drbecker.jobs/', 0 -> 75 rows). Asklepios (AC#1) and Rotkreuzklinik Würzburg (AC#2) were already fully resolved with no delivery gap. All 3 boards now confirmed live in production.
<!-- SECTION:FINAL_SUMMARY:END -->
