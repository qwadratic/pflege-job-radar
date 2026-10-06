---
id: TASK-438
title: >-
  Luna answers a pooled candidate's questions about her own status from the
  handoff states, with the status link
status: To Do
assignee:
  - wa-harness
created_date: '2026-10-06 08:54'
labels:
  - whatsapp
  - luna
dependencies: []
priority: medium
ordinal: 314000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06, relayed by pflege-fe: after a candidate is in the pool, Luna must accept her questions about her own status ("how are things going"). Luna answers only from the per-clinic handoff states Daria posts to wa_handoffs (TASK-396 HandoffStatus), plus the candidate's status-page link (TASK-436). Nothing invented. The link is appended by code, never written by the model (URL ban, TASK-373). Requested by the WhatsApp lane (wa-harness).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A pooled candidate's status question gets an answer grounded in her wa_handoffs rows; covered by offline tests with seeded rows
- [ ] #2 No handoff rows means an honest "nothing to report yet", without invented progress
- [ ] #3 The status link is code-assembled and appears only when a published status page exists for her
- [ ] #4 LLM persona tests for both cases
<!-- AC:END -->
