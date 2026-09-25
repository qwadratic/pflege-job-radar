---
id: TASK-122
title: >-
  Re-check karriere.bezirkskrankenhaus-lohr.de (66104/66105) once the board
  carries more than one posting
status: To Do
assignee: []
created_date: '2026-09-23 03:17'
updated_date: '2026-09-25 00:10'
labels:
  - crawler-coverage
dependencies:
  - TASK-118
priority: low
ordinal: 122000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Board (karriere.bezirkskrankenhaus-lohr.de, shared by clinic 66104 Klinikum Lohr am Main and 66105 Klinikum Aschaffenburg) has more than the single posting it had on 2026-09-23
- [ ] #2 Re-verify each posting's employer_name resolves it to the correct clinic_id (66104 vs 66105), not a default
- [ ] #3 If a genuinely ambiguous or explicitly multi-site posting shows up (title names both towns, no clinic-specific employer_name), decide whether to link it to both clinic_ids or pick one, and implement
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Follow-up to TASK-118 case 2. On 2026-09-23 this board had exactly ONE real posting (10076), already correctly attributed to 66104 by its own unique employer_name ("Tagesklinik Aschaffenburg des BKH Lohr am Main") -- no defaulting bug, nothing to fix. TASK-118's own AC#1 (multi-site posting handling) was left unchecked because the premise didn't hold with only one posting on the board. Both 66104 and 66105 are small clinics with thin posting volume -- revisit once the board actually has enough postings to show whether a real disambiguation problem exists.
<!-- SECTION:NOTES:END -->
