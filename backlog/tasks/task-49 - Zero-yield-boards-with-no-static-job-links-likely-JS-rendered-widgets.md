---
id: TASK-49
title: Zero-yield boards with no static job links -- likely JS-rendered widgets
status: To Do
assignee: []
created_date: '2026-09-11 10:49'
labels: []
dependencies: []
ordinal: 49000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-11 recon as TASK-48, but this bucket (20 boards) has ZERO JOB_PATH-matching hrefs in the plain-fetched HTML at all, despite the page mentioning 'pflege' and returning HTTP 200. These are candidates for either a client-side widget with no server-rendered fallback (needs Playwright or the widget's own AJAX endpoint, same shape as pflege_jobs/sources/beesite.py's approach), a genuinely empty board right now, or a stale careers_url that no longer points at the real listing. Two are large beds totals worth prioritizing: klinikverbund-allgaeu.de (1048 beds) and klinikum-ab-alz.de (831 beds).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each board confirmed as one of: JS-widget (needs Playwright/API probe), genuinely 0 open postings right now, or wrong careers_url (fix directly)
- [ ] #2 boards + beds from recon: klinikverbund-allgaeu.de(1048) klinikum-ab-alz.de(831) www.frg-kliniken.de(365) www.kliniken-nea.de(316) www.waldkrankenhaus.de(290) kbo-dak.de(275) www.kh-nuernberger-land.de(257) wertachkliniken.de(256) www.reisach-kliniken.de(251, 2 distinct board urls both zero) www.klinik-vincentinum.de(200, NOT a bug -- real jobs already covered via the shared Artemed smartrecruiters board, this is a decoy SmartRecruiters JS widget on the clinic's own page, see registry board ['18872','18105','18802','18808','76108']) hire.klinikum-fuenfseenland.de(130) www.klinik-bad-trissl.de(120) www.artemed-muenchen-sued.de(110) www.kreiskrankenhaus-hoechstadt.de(80) www.st-irmingard.de(75) klinik-menterschwaige.de(62) klinik-wirsberg.de(50) www.clinic-dr-decker.de(45) www.klinik-am-birkenwald.de(40) www.fachklinikum-mainschleife.de(40)
- [ ] #3 www.klinik-vincentinum.de excluded from further action -- confirmed not a bug, its board already covered elsewhere
<!-- AC:END -->
