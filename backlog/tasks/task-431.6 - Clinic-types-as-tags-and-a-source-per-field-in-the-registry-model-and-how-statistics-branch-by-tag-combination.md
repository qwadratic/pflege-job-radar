---
id: TASK-431.6
title: >-
  Clinic types as tags and a source per field in the registry: model and how
  statistics branch by tag combination
status: To Do
assignee: []
created_date: '2026-10-06 07:58'
labels:
  - registry
  - data-quality
dependencies:
  - TASK-431.1
  - TASK-431.3
parent_task_id: TASK-431
priority: medium
ordinal: 311000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06: a clinic can have several types, so type is a set of tags, not one value; statistics then branch by the combination of tags. Today type is spread over status (Plan-KH, Reha-Einrichtung, HS-Klinik, ...), traegerart, versorgungsstufe and size, and acute and Reha must not be pooled in one ratio (TASK-431.3: 2.71 versus 0.42 postings per 100 beds). Design: (1) tag vocabulary (acute, reha, day-clinic, psychiatry, social, university U, optional academic A, size S/M/L, plus the existing operator group), each tag with its rule and source; (2) where tags live (derived at read time like size, or stored with a source and date); (3) a source per registry field value, since the registry is composed from several sources (Krankenhausplan, RHV, hand corrections, open-source re-checks, TASK-431.5); (4) a table of statistics (jobs per 100 beds, share with postings, board coverage) with the tag combination that decides how each is computed. Research and design only; no migration before Ivan approves.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Tag vocabulary with rules and counts per tag and per frequent combination over the 651 clinics
- [ ] #2 Decision on derived versus stored tags and on per-field source recording, with the effect on the public API
- [ ] #3 Statistics table: which statistic uses which tag combination
<!-- AC:END -->
