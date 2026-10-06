---
id: TASK-309
title: Cut WhatsApp reply latency
status: To Do
assignee: []
created_date: '2026-09-25 17:56'
updated_date: '2026-09-26 08:48'
labels: []
dependencies: []
project: whatsapp
ordinal: 262000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A warming turn took 138 s at effort max. Measured parts: brain 9 s at high, closing-gate Haiku call 9-12 s per call (mostly CLI spawn), per-turn truth-filter rewrites used to double the turn (removed by the correction-turn task). Ideas only so far: lower brain effort, keep a warm CLI process for the gate, run the gate concurrently with post-processing, measure end to end from inbound notification to outbound tick.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 End-to-end latency measured from inbound capture to send on the live rail, broken down by stage
- [ ] #2 Each speed idea tried is measured before/after with the same scenario
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26: folded into TASK-313 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
