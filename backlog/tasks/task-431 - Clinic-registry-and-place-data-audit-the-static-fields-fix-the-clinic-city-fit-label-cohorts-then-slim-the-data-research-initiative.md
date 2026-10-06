---
id: TASK-431
title: >-
  Clinic registry and place data: audit the static fields, fix the clinic-city
  fit, label cohorts, then slim the data (research initiative)
status: To Do
assignee: []
created_date: '2026-10-06 07:19'
labels:
  - registry
  - data-quality
dependencies: []
priority: medium
ordinal: 301000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06: before vacancies, the objective static data of a clinic must be clean and measurable. Today the registry (651 clinics) has size S/M/L/XL (309/247/70/12, 13 empty), status incl. HS-Klinik (7), beds (638/651), versorgungsstufe (409/651), plz 0/651, employer_id 0/651, lat/lon only from the municipality centroid of town; the postings carry city+plz (755 distinct pairs, 572 cities) and do not line up with the 287 clinic towns (354 of 570 posting cities match no clinic town by string; not yet classified). Goal: know where each static field comes from, how complete and how correct it is, make the place of a posting and the place of a clinic meet in both directions, label cohorts (size, university) by a written rule, and only then cut what is not needed. Published data stays as it is until the objective replacement exists; the cut is the second pass. Related and not to be duplicated: TASK-143 (beds verification), TASK-167 (beds NULL), TASK-184 (university and 10k-bed bands in the landscape), TASK-200 (lat/lon), TASK-190. Any DB write needs Ivan's approval of the exact counts first.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The four child tasks are done and their results are written in this task's final summary
<!-- AC:END -->
