---
id: TASK-449
title: >-
  next_step on every card, a brain tool to set it, and a selective follow-up
  picker instead of the fixed nudge series
status: To Do
assignee: []
created_date: '2026-10-07 15:16'
labels:
  - dialog
  - architecture
dependencies:
  - TASK-314
priority: high
project: whatsapp
ordinal: 329000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Each brain run judges the conversation, may amend the candidate card, and sets next_step with a tool: what we expect from her, or the single follow-up planned. The next turn sees next_step with the card scoreboard. A periodic job lists the silent candidates; a model decides whom to follow up, exactly one message each, no pre-scheduled series. The fixed nudge series (tiers 15/60/240, cap 4; TASK-420) is removed. Name the field next_step, not agent_note (agent_note is taken by the operator-note gate). Requested by the WhatsApp harness lane (wa-harness) through pflege-clawl, 2026-10-07; source: Ivan 2026-10-07, notes already on TASK-314 and TASK-316 (branch docs/backlog-next-step-and-temperature). No candidate data in this text; German candidate wording only with Ivan's verbatim approval.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The brain has a tool that sets next_step on the card; the next turn shows it next to the card scoreboard
- [ ] #2 A periodic job lists silent candidates and a model picks whom to follow up, one message each, with no series scheduled in advance
- [ ] #3 The fixed nudge series (tiers 15/60/240, cap 4) is gone from followups.py, with its tests replaced
- [ ] #4 next_step is the field name everywhere; agent_note stays with the operator-note gate
<!-- AC:END -->
