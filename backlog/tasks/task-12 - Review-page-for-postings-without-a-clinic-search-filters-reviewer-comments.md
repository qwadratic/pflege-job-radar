---
id: TASK-12
title: >-
  Pro: Raw data section with a tab per entity, cross-linked; postings without a
  clinic as one tab
status: To Do
assignee: []
created_date: '2026-09-09 11:12'
updated_date: '2026-09-09 11:36'
labels:
  - frontend
dependencies: []
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan (2026-09-09): this lives only in the /pro dashboard, under a Raw data section, with one tab per entity (clinics, operators, boards, loads, postings, unmatched postings, harvest_report) and cross-links between them -- a posting links to its clinic and operator and load, a clinic to its board and operator and events, a board to its loads and report rows. The unmatched-postings review is one of those tabs. Background: 254 of 2,146 live postings have clinic_id NULL. Measured buckets (2026-09-09): 142 are entities outside the Krankenhausplan registry (Diakoneo Wohnen, Artemed Reha, MVZ), 46 are registry clinics whose name form differs from the registry (Arberland, Kreiskrankenhaus, ss/ß), 36 name an operator with several sites and no town, 6 are outside Bavaria, 24 are extraction artefacts. The unmatched tab shows the matcher's candidate clinics and scores where any exist and carries a per-posting reviewer comment (Claude or a human) recording why it is unmatched and what would resolve it. The bucket classification should become an API field, not client-side logic.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A /pro page lists every posting with clinic_id NULL, with search over title, employer and city
- [ ] #2 Each row shows the matcher bucket, the candidate clinics with their scores when the matcher found any, and a free-text reviewer comment that persists
- [ ] #3 Filters by bucket, employer, city and Land work without a page reload
- [ ] #4 Raw data section exists only in /pro, with one tab per entity and cross-links between entities
- [ ] #5 The unmatched-postings tab lists every posting with clinic_id NULL, searchable over title, employer and city, with bucket, candidate clinics and scores, and a persistent reviewer comment
- [ ] #6 Filters by bucket, employer, city and Land work without a page reload
<!-- AC:END -->
