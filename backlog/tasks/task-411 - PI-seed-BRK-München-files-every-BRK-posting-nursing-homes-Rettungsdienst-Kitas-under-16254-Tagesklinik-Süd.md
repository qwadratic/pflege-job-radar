---
id: TASK-411
title: >-
  P&I seed BRK München files every BRK posting (nursing homes, Rettungsdienst,
  Kitas) under 16254 Tagesklinik Süd
status: To Do
assignee: []
created_date: '2026-09-29 23:15'
updated_date: '2026-10-05 13:36'
labels:
  - adapter
  - matcher
dependencies: []
ordinal: 178000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-29 by the TASK-178 P&I work (worktree ki-pi): data/registry/pi_seeds.json 'BRK München' (brkm.pi-asp.de, param company=123-FIRMA-ID) has default kez 16254 and no sites, so every row of the BRK Kreisverband München board -- Pflegeheime, Rettungsdienst, Kitas -- is attributed to the day clinic 16254, e.g. live posting 13591. The TASK-178 list fix (pi_asp._list_rows no longer drops titles without '(m/w/d)') reads 18 of 18 BRK rows instead of 15, so 3 more rows join that pile once it is deployed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every BRK board row is either matched to the registry site it names or left without a clinic (kez null site), with the board's own pin/department line as evidence
- [ ] #2 Existing wrongly linked BRK postings under 16254 are unlinked via tools/apply_posting_changes.py with code wrong_clinic (needs Ivan's go-ahead)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-05: renumbered from TASK-181 by backlog doctor --fix (two tasks had the ID TASK-181). A mention of TASK-181 in a task text written before this date may mean this task, not the one that kept TASK-181.
<!-- SECTION:NOTES:END -->
