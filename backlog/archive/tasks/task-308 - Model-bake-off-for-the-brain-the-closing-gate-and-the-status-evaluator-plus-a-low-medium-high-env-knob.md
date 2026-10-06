---
id: TASK-308
title: >-
  Model bake-off for the brain, the closing gate and the status evaluator, plus
  a low/medium/high env knob
status: To Do
assignee: []
created_date: '2026-09-25 17:56'
updated_date: '2026-09-26 08:48'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 261000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Brain moved Opus 5 -> Sonnet 5 on 2026-09-25 without a measured comparison. Measured S2 warming turn on Sonnet 5: effort max = 125 s brain / 138 s turn, high = 9 s / 24 s, medium = 9 s / 24 s (medium re-introduced 'Ich bin Valentina' mid-thread). Ivan wants price/quality/latency compared before choosing: Sonnet at several efforts vs Haiku at max vs Opus 5.5 at several efforts; the same for the closing gate and the TASK-299 status evaluator. Result feeds one coarse env knob.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Same fixed scenario set replayed per model/effort, no live sends; latency, token cost and a quality verdict recorded per cell
- [ ] #2 Brain, closing gate and status evaluator each get a recommended model/effort with the evidence
- [ ] #3 One env knob with values low / medium / high maps to the chosen model/effort pairs
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26: folded into TASK-313 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
