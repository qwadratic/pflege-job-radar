---
id: TASK-398
title: >-
  Agent-note hand-off: trust the SendMessage tool result, not the model's
  self-report
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
labels:
  - agent-notes
dependencies:
  - TASK-303
priority: medium
type: bug
project: whatsapp
ordinal: 273000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Open design risk from the TASK-303 build (2026-09-25): the hand-off counts as done when the model says so. The SendMessage tool result is not checked, so a note can be handed over twice or lost.

TASK-303 AC4/AC6 cover a failed hand-off via the ListAgents match count, not the send result itself.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Hand-off success is read from the SendMessage tool result
- [ ] #2 A failed or unconfirmed send is retried at most once, then flagged
- [ ] #3 A test covers the duplicate hand-off case
<!-- AC:END -->
