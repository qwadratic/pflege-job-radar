---
id: TASK-53
title: 'Klinikum Passau: job postings are PDF attachments, no HTML detail pages'
status: Done
assignee: []
created_date: '2026-09-11 10:49'
updated_date: '2026-09-11 11:56'
labels: []
dependencies: []
ordinal: 53000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Klinikum Passau (clinic_id 26201, 660 beds, single-clinic board) publishes every open role as a PDF file under bewerbung.klinikum-passau.de/dateiablage/stellen/<id>/<slug>.pdf, linked directly from the /beruf-karriere/offene-stellen listing page via a bespoke TYPO3 extension (typo3conf/ext/klinikumpassau_joboffers). There is no per-job HTML detail page and no JSON-LD anywhere -- confirmed live 2026-09-11, PDF filenames include 'ausbildungpflegeschulepflegefachfraumann', 'saoaorthopdie', 'saergotherapeutneurologieakutgeriatrie', so real nursing/ausbildung postings exist but crawl_wp_jobs currently returns raw=1 (the listing page itself, misparsed) / kept=0. This is Bavaria's single largest unfixed board by beds among the 2026-09-11 zero-yield boards.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 PDF filenames/link text parsed into title + role_class without opening the PDF body (the filename itself is descriptive enough for classify_role, e.g. 'ausbildungpflegeschulepflegefachfraumann20')
- [ ] #2 OR: PDF text extracted (title + department) if filename-only classification proves too lossy
- [x] #3 Klinikum Passau's real open postings appear in prod
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause was simpler than assumed at task creation: the listing page itself (klinikumpassau_joboffers TYPO3 extension) already renders each posting's full title + department + description inline in plain HTML (<span class="vacancy-header">, <h2 class="job_group_headline">, <div class="vacancy-description">) -- the PDF link is just an optional attachment, never needed. Wrote pflege_jobs/sources/klinikum_passau.py to parse that structure directly (regex-based, matching this codebase's existing adapter style), wired it in as a new ats_type 'klinikum_passau' (crawlers/routing.py ADAPTERS + app/crawl.py _seed_obs dispatch), and set clinic 26201's ats_type in both the DB and data/registry/clinics.csv. Delivered live 2026-09-11: raw=18, kept=4 nursing/ausbildung postings, 4/4 matched to clinic 26201 (R1_exact), 3 newly created.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Klinikum Passau's real fix needed no PDF parsing: pflege_jobs/sources/klinikum_passau.py parses the listing page's own inline title/department/description HTML. New ats_type 'klinikum_passau' wired into routing.py + app/crawl.py._seed_obs. Verified live: 4 real nursing postings delivered to prod (3 new), 4/4 matched to clinic_id 26201.
<!-- SECTION:FINAL_SUMMARY:END -->
