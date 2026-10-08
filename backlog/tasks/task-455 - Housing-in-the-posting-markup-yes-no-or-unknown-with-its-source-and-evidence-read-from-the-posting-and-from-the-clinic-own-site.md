---
id: TASK-455
title: >-
  Housing in the posting markup: yes, no or unknown, with its source and
  evidence, read from the posting and from the clinic own site
status: To Do
assignee: []
created_date: '2026-10-08 12:07'
labels:
  - matching
  - data-quality
dependencies: []
priority: high
ordinal: 337000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-08: postings must be marked up well for matching with a CV, and whether accommodation is given must be in the markup; the source is not only the posting text, the clinic's own site can state it too. Today (measured 2026-10-08 on 3498 open postings): enr_housing is true for 438 (12.5 percent), false for 2003 (57.3 percent) and null for 1057 (30.2 percent), and false only means that the pattern did not match, so 'not mentioned' and 'no housing' are the same value; the 1063 postings without a description are almost all null (1057 null in total). Clinics have no housing field at all (the clinics table has no such column). Wanted: housing is one of yes, no, unknown; yes/no only with evidence (the quoted phrase, the page url, the date); a statement on the clinic's own site (staff housing, Personalwohnheim, Wohnheim for trainees) becomes a claim of the clinic that applies to its postings unless the posting says otherwise, and the posting keeps the source of the value it shows. Fits the claim-and-evidence catalogue of TASK-441; do not build a second mechanism. Related: TASK-437 (the candidate side yes/no for housing in Luna), TASK-431.1 (audit of static clinic fields).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 enr_housing stops conflating not mentioned and no: three values, with a migration and a test for each pattern class, and the existing 438 true values keep their evidence
- [ ] #2 A clinic-level housing claim with url, quote and date exists for the clinics whose own site states it; a posting shows the value, its source (posting or clinic site) and the evidence
- [ ] #3 Counts before and after by value and by source are given to Ivan; nothing is written to the database before he approves the exact counts
- [ ] #4 The API and the matching read the three-valued field; the candidate-side answer can say yes, no or we do not know
<!-- AC:END -->
