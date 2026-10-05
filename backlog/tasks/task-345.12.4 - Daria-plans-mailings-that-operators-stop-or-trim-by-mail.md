---
id: TASK-345.12.4
title: Daria plans mailings that operators stop or trim by mail
status: To Do
assignee: []
created_date: '2026-10-01 18:10'
updated_date: '2026-10-01 18:42'
labels:
  - email
dependencies: []
parent_task_id: TASK-345.12
priority: medium
ordinal: 282000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-01: "должна также уметь планировать рассылки, которые можно стопнуть или исключить клиники отмашкой". Today a mailing is planned by Claude in a session and started live by Ivan only (approval file plus his own live run). Whether an operator go-ahead by mail may replace his live run is his decision and is open.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 On request Daria plans a scheduled batch (clinics, letters, cadence, announcement) and sends the plan to the operators
- [ ] #2 Operators stop it or take clinics out by mail before and during the run
- [ ] #3 The live start follows the rule Ivan sets; until he changes it, Daria never starts a live batch
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Ivan, 2026-10-01: a go-ahead by mail does NOT replace his own live run ("нет"). Daria may plan and present a mailing; only Ivan starts it live.
<!-- SECTION:NOTES:END -->
