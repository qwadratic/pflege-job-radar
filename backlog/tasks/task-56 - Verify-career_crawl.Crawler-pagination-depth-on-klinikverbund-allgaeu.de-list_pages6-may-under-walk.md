---
id: TASK-56
title: >-
  Verify career_crawl.Crawler pagination depth on klinikverbund-allgaeu.de
  (list_pages=6 may under-walk)
status: To Do
assignee: []
created_date: '2026-09-11 14:05'
labels: []
dependencies:
  - TASK-49
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Surfaced 2026-09-11 while fixing Klinikum Kempten's careers_url as part of TASK-49 (klinikverbund-allgaeu.de, 1048 beds combined across 6 clinics: Kempten/Mindelheim/Ottobeuren/Immenstadt/Oberstdorf/Sonthofen). The shared board is a Haufe umantis instance embedded behind the clinic group's own domain (karriere.klinikverbund-allgaeu.de and karriere-im.klinikverbund-allgaeu.de, both the SAME recruitingapp-5556 instance). app/crawl.py's _seed_obs routes vendor=='umantis' through pflege_jobs.sources.career_crawl.Crawler(towns, per_site_pages=150, list_pages=6, sleep=0.2).crawl(seed) -- a full delivery run against this board returned raw=10 / job_links_found=10 total across all 6 clinics combined, which felt low: a single page fetch of the board's own /karriere-detail/... links already showed more than 10 distinct postings across just Immenstadt+Kempten+Mindelheim in the HTML grabbed during recon (see crawlers/vendor_adapters.py's job-link discovery pattern for comparison -- this board is NOT routed through crawl_wp_jobs, it goes through career_crawl.Crawler instead, a different code path). Not confirmed as a bug -- could genuinely be all there is right now -- but list_pages=6 stopping the walk while the portal's own listing may paginate further is the concrete thing to check first.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Confirm live (fresh fetch, not cached) how many total distinct /karriere-detail/ postings klinikverbund-allgaeu.de's umantis board actually has right now, across all 6 Bavaria clinics
- [ ] #2 If the real count is higher than what career_crawl.Crawler returns, find why -- list_pages ceiling too low, a pagination link/pattern the Crawler's _next_page detection doesn't recognize on this specific portal, or the section-first BFS narrowing before it should widen back out
- [ ] #3 Fix and redeliver if a real gap is confirmed; otherwise close with a note that raw=10 is the real current total
<!-- AC:END -->
