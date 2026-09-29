---
id: TASK-396
title: 'Handoff has no done status: consented leads stay ''needs a human'' forever'
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
labels:
  - pro-api
  - needs-ivan
dependencies: []
priority: medium
type: feature
project: whatsapp
ordinal: 271000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
wa_queue_candidates.status is only ever "queued", so the Pro Leads view counts every consented lead as needing a human, forever. pflege-fe raised this on 2026-09-29.

Phase 1 of the Pro API is read-only, and marking a handoff done is a write. Whether and how it gets marked is Ivan's call: an operator action, an email sent, or a clinic reply.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ivan decides whether a handoff gets a done state and what sets it
- [ ] #2 Implemented, with the state exposed in the Pro API rows, or closed with his decision
<!-- AC:END -->
