---
id: TASK-85
title: >-
  Adapter silently returns near-zero rows against a live board and reports
  success: 13 clinics, ~4000 beds
status: To Do
assignee: []
created_date: '2026-09-21 04:26'
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
- [ ] #2 /sitemap-index.xml is in the sitemap candidate list, and a WordPress board whose custom post type is missing from the sitemap falls back to /wp-json/wp/v2/<cpt>?per_page=100; 27501 and 57705 yield their real counts (27 and 53)
- [ ] #3 A board whose vacancies live off-host is followed to that host when the registry/board itself points there (56201 -> jobs.malteser.de yields its 13 nursing rows)
- [ ] #4 jobs.bezirkskliniken-schwaben.de is read via its inline JSON model, serving 76114/76203/77406 from one adapter with no render and no Firecrawl
- [ ] #5 66101's Knockout SPA board (jobs.klinikum-ab-alz.de, 62 jobs) yields its rows
- [ ] #6 crawl_wp_jobs terminates on the 778-job AMEOS board (18501) within a sane bound, and whatever caused the non-termination is named
<!-- AC:END -->
