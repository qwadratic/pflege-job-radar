---
id: TASK-431.6
title: >-
  Clinic types as tags and a source per field in the registry: model and how
  statistics branch by tag combination
status: To Do
assignee: []
created_date: '2026-10-06 07:58'
updated_date: '2026-10-06 11:11'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-06 Ivan: the size index is SML, with its own algorithm inside each clinic type (acute and Reha not pooled), and the claim S/M/L exists for every clinic, backed by evidence rows (TASK-441). The tag vocabulary and the statistics table of this task are the type part of that design; the evidence and provenance part is TASK-441.

2026-10-06 CORRECTION (Ivan): SML and clinic type are two different things. SML is only the size of the establishment. Type is what the clinic has and specialises in: its sets of departments, its specialisation, university as a type of its own. Purpose of type: which vacancies we meet where depends on it, and the candidate search (for which candidate which clinic) will learn from successful hires by type later, so type needs a structure now. Therefore: type is a set of tags (TASK-431.6), size is a claim S/M/L (TASK-441) whose algorithm may differ inside a type, but size is not a tag. Statistics branch by tag combination as before.
<!-- SECTION:NOTES:END -->
