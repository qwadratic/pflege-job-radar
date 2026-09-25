---
id: TASK-301
title: Follow-ups become a real model turn built from the card's open question
status: To Do
assignee: []
created_date: '2026-09-25 00:01'
labels: []
dependencies:
  - TASK-299
priority: high
project: whatsapp
ordinal: 254000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24: the follow-up system needs redesigning, not tuning. Today a nudge is a fixed string sent to someone who went quiet. He wants a cadence over EVERY active conversation, where each tick assembles context and takes one real turn: up to the last 30 bubbles verbatim, everything older summarised, the card state, and the one currently open question -- then sends exactly one message aimed at that question. Silence becomes legal only under an operator mute or an explicit opt-out (TASK-299), never as a side effect of nobody having replied. This is the mechanism behind his first success criterion: candidates who answer the broadcast or knock on the bot must actually advance along the card rather than stall.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The cadence covers every active conversation, not only threads that have gone silent
- [ ] #2 Each tick builds context from at most the last 30 bubbles verbatim plus a summary of everything older, the card state, and the single open question
- [ ] #3 Exactly one message is sent per conversation per tick, and it addresses the open question
- [ ] #4 Silence happens only under mute or an explicit opt-out; every other state produces a message
- [ ] #5 The reply goes through the closing gate like any other turn, so the last bubble hands the turn back
- [ ] #6 The old fixed nudge strings are removed, not merely bypassed
- [ ] #7 A test proves a conversation that never went silent still receives its cadence turn
<!-- AC:END -->
