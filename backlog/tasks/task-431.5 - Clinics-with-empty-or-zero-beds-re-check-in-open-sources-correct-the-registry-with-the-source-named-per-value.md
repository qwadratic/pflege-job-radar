---
id: TASK-431.5
title: >-
  Clinics with empty or zero beds: re-check in open sources, correct the
  registry with the source named per value
status: To Do
assignee: []
created_date: '2026-10-06 07:58'
labels:
  - registry
  - data-quality
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 310000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
63 clinics have no usable beds: 13 with beds NULL (Diakoneo social, DK01..DK13), 42 with beds 0 that are day-place-only (Plan-KH 40, Vertrags-KH 2) and 8 Bedarfsfeststellung with 0 planned beds (findings of TASK-431.3, 2026-10-06). Ivan: re-check these in open sources and, where a number is found, correct the registry and record in which source it was found; the registry is composed from several sources and every value should say where it came from; different sources may differ for a reason (set-up beds, planned beds, day places, Reha beds versus acute). Candidate sources: the clinic's own site and imprint, the structured quality report (G-BA Qualitaetsbericht, Bettenzahl), Krankenhausplan 2026 (planned beds and day places), Destatis KHV, RHV list for Reha, operator pages. Result of this task is research: per clinic the found value, the kind of number (planned, set-up, day places, places in a social facility), the source URL and date, and a proposed registry change; no DB write before Ivan approves the exact counts. Also check the 138 of 259 size-S clinics under 50 beds whose bed number looks like a day-clinic fragment, as a sample.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Per clinic of the 63: found value, kind of number, source URL and date, or a named reason that nothing public exists
- [ ] #2 Proposal for how the registry records the source of a bed number (field or table), consistent with the corrections table of TASK-180
- [ ] #3 DB writes only after Ivan approves the exact counts
<!-- AC:END -->
