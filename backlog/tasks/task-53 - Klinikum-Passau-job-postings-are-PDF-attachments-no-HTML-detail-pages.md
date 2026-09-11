---
id: TASK-53
title: 'Klinikum Passau: job postings are PDF attachments, no HTML detail pages'
status: To Do
assignee: []
created_date: '2026-09-11 10:49'
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
- [ ] #3 Klinikum Passau's real open postings appear in prod
<!-- AC:END -->
