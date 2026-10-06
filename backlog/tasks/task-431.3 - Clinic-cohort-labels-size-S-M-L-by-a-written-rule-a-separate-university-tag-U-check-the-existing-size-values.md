---
id: TASK-431.3
title: >-
  Clinic cohort labels: size S/M/L by a written rule, a separate university tag
  U, check the existing size values
status: To Do
assignee: []
created_date: '2026-10-06 07:20'
labels:
  - registry
  - data-quality
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 304000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Registry today: size S 309, M 247, L 70, XL 12, empty 13; status HS-Klinik 7 (university hospitals); versorgungsstufe filled for 409 only. Ivan wants cohorts S, M, L by clinic size and a separate tag U for university hospitals (they are a different kind of clinic and should be L or carry U on top). Write the rule (which field and thresholds: beds, versorgungsstufe, status), check the 638 existing size values against it and list the differences, decide what XL means in relation to L, add the U tag from status HS-Klinik and a reviewed list (Bavaria has five university hospital sites plus the Augsburg one that became a university hospital; verify from the source, do not assume). Keep it consistent with TASK-184 (university separately, 10k-bed bands) and TASK-143 (beds correctness). The 13 clinics without beds get a label or an explicit named reason.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Written rule for S/M/L (and XL if kept) and for U, in docs or the registry module
- [ ] #2 List of clinics whose current size differs from the rule, with counts per cohort before and after
- [ ] #3 U tag set for every university hospital and tested; DB writes only after Ivan approves the exact counts
<!-- AC:END -->
