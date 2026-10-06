---
id: TASK-345.12.6
title: >-
  Weekly health report from Daria on our code and infra: repo, Mac mini, server,
  services, logs
status: To Do
assignee: []
created_date: '2026-10-01 18:54'
updated_date: '2026-10-06 12:50'
labels:
  - email
  - infra
dependencies: []
parent_task_id: TASK-345.12
priority: medium
ordinal: 285000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-01: "также еженедельный апдейт по здоровью нашего кода (репо) и инфры (мини, сервер, сервисы, логи)". Nobody looks at the whole state on a schedule: failures sit in logs until someone trips over them (for example TASK-392, a relay that crash-loops on a port clash). Daria (TASK-345.12) has no shell, because a forged operator address must not reach the server, and she never touches the phone, the bridge or the Mac mini (Ivan, the same day, for WhatsApp: "сам телефон не трогает"). So every fact she reports must reach her read-only. Open with Ivan before work starts: the day and time, and whether the parallel operator gets it too.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Once a week, at the day and time Ivan chose, the chosen operators get one mail from Daria on the state of the code and the infra
- [ ] #2 The report covers the repo, the server, each of our services, the Mac mini and the logs of the week; each problem says since when and names the backlog task that tracks it, or says none does
- [ ] #3 A source that could not be read is named in the report as not read; it is never left out
- [ ] #4 Collecting the report changes nothing: no write to the repo, a service, the phone rail or the Mac mini, and Daria still has no shell, verified by a test
<!-- AC:END -->
