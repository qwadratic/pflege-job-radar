---
id: TASK-431.4
title: >-
  Second pass: slim the static registry data to what is read, after the audit
  and the place fit
status: To Do
assignee: []
created_date: '2026-10-06 07:20'
labels:
  - registry
  - data-quality
dependencies:
  - TASK-431.1
  - TASK-431.2
  - TASK-431.3
parent_task_id: TASK-431
priority: low
ordinal: 305000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Only after TASK-431.1, 431.2 and 431.3 are done: cut static clinic data that nothing reads and that is not objective (fields found unused or wrong in the audit, duplicate place data, the posting-observation snapshot reduced to its distinct pairs if the tests still hold). Data already published stays until the objective replacement exists; things kept for completeness are fine if they are objective. Each cut lists what goes, who read it (audit table) and what test proves nothing broke. Ivan approves the cut list before anything is deleted.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Cut list with reader evidence per field, approved by Ivan before any deletion
- [ ] #2 After the cut the full offline suite and the mirror suite are green and the public API fields in use are unchanged
<!-- AC:END -->
