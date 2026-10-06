---
id: TASK-431.5
title: >-
  Clinics with empty or zero beds: re-check in open sources, correct the
  registry with the source named per value
status: To Do
assignee: []
created_date: '2026-10-06 07:58'
updated_date: '2026-10-06 14:04'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-06 Ivan raised the registry model to architecture level: the `clinic_numbers` proposal of this task becomes the first version of the evidence catalogue of TASK-441 (claim S/M/L backed by evidence rows, each with kind, source, how collected, URL, date seen, agent note). The research result of this task stands (63 clinics, report in the job directory of pflege-clawl); how the numbers are recorded is decided in TASK-441, DB writes still wait for Ivan approving the exact counts.

2026-10-06 DECIDED by pflege-clawl after the impact check (Ivan: "yes, re-check, decide yourself when sure"). (1) 36302 Weiden is NOT hand-overridden from 0 to 32 beds: the registry holds the approved number of the Krankenhausplan 2026 (0 beds, 12 places) by design, the operator page (32 beds in operation since April 2026, article of 2026-07-10) is a different kind of number, and its day places (18) differ from the plan target (33), so the match is medium. It goes in as an evidence row (kind beds_reported, beds_planned) when the catalogue of TASK-441 exists. Impact avoided: one clinic and one open posting; size would move None to S (S 259 to 260, None 63 to 62). No DB write. (2) The 8 planned values (plan columns "in Planung") become evidence rows of kind beds_planned and places_planned in TASK-441, not new columns of clinics: no existing consumer, no impact today. (3) Diakoneo (13): clinics.beds stays NULL and size stays no_bed_concept; the places (stated or derived x + 2y) become evidence rows of kind places_social. No impact on size or statistics today. (4) Vertrags-KH rows as fragments of a Plan-KH site: the principle is right (size is the size of the site), but impact is 6 rows of 651 (S 259 to 254, M 247 to 250, L 82 to 84) for the 4 verified sites, 16 more rows are unverified, and the site key does not exist yet. Deferred to TASK-441 (site groups come with the evidence catalogue); no code now.
<!-- SECTION:NOTES:END -->
