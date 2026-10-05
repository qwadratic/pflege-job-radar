---
id: TASK-190
title: >-
  Big hospitals with very few open postings: 24 units of 250+ beds under 10 per
  1000 beds are adapter-completeness candidates
status: To Do
assignee: []
created_date: '2026-10-01 18:05'
labels:
  - adapter
  - verify-freshness
dependencies: []
priority: medium
ordinal: 187000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
From the TASK-184 cohort table (2026-10-01). Suspicion list only, never a rule: units of 250+ beds with the lowest clean vacancies per 1000 beds: InnKlinikum Muehldorf 275 beds 0 vacancies, Kreiskrankenhaus Freyung 260 / 0, Psychosomatische Klinik Bad Neustadt 251 / 0 (rexx), Rotkreuzklinikum Muenchen 435 / 1, Bezirksklinikum Obermain 302 / 1, Bezirkskrankenhaus Lohr 295 / 1, Schoen Klinik Bad Aibling 287 / 1, ZPG Ingolstadt 275 / 1, Kreiskrankenhaus Eggenfelden 275 / 1, St. Barbara Schwandorf 267 / 1, kbo-Inn-Salzach-Klinikum Wasserburg 518 / 2, Bezirksklinikum Mainkofen 562 / 3, Bezirksklinikum Ansbach 377 / 2, Sana Klinikum Lichtenfels 276 / 2, Schoen Klinik Roseneck 413 / 3, Kreisklinik Roth 270 / 2, OKH Schloss + BKH Werneck 390 / 3, Bezirksklinikum Regensburg 623 / 5, Muenchen Klinik (5 sites) 2,896 / 24 (8.3), DONAUISAR Klinikum Deggendorf 465 / 4, Klinikum Passau 660 / 6, BKH Augsburg 326 / 3, kbo-Isar-Amper-Klinikum Muenchen-Ost 750 / 7, BKH Guenzburg 422 / 4. Psychiatric and rehab houses may simply hire little; the point is to compare each board with the adapter output. Related: the sweep of 42 acute groups of 100-299 beds with 0 open (session task 7), TASK-88 completeness alarm.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 For each unit: adapter output compared with the boards own self-reported total (TASK-88 method); verdict complete, or the missing postings and the adapter fix
- [ ] #2 Units whose zero or near-zero is real (small turnover, board empty) marked as checked with the date and the evidence, so the list shrinks
<!-- AC:END -->
