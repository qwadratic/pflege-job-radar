---
id: TASK-283.7
title: 'Pro activity tab: rail, phone_ops queue and job health (read-only)'
status: To Do
assignee: []
created_date: '2026-10-01 14:23'
labels: []
dependencies:
  - TASK-395
parent_task_id: TASK-283
priority: high
project: whatsapp
ordinal: 276000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-01: "я хочу чтобы на борде активности в pflege fe была также картинка по бриджу, очереди, как поживают автоматические и взятые в очередь задачи". Read slice of TASK-283.1 and the read half of 283.3. pflege-fe builds it as the #/leads?tab=rail tab. Plan and agreed shape: ~/plans/2026-10-01-pro-activity-rail-view.md. Pro reads SQLite only: the engine (relay_pull) mirrors the bridge health and every phone_ops row into wa.sqlite. The bridge gains a read-only GET /v1/ops and an origin column.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GET /api/wa/pro/activity and /api/wa/pro/ops answer from wa.sqlite only, with snapshot_at on every rail fact
- [ ] #2 phone_ops carries origin; every Client entrypoint sets it
- [ ] #3 The ops list has no caps: cursor paging over the full mirror; counts are complete
- [ ] #4 No raw phone, wamid or message text in either endpoint
- [ ] #5 /api/wa/pro gated owner-only on the board, with anon and customer deny tests
- [ ] #6 Contract and fixtures in docs/wa-dashboard.md; pflege-fe notified
<!-- AC:END -->
