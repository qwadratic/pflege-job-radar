---
id: TASK-451
title: >-
  Manager contact only through a tool gated on card level: warning first, a
  concrete call rare
status: To Do
assignee: []
created_date: '2026-10-07 15:17'
labels:
  - dialog
dependencies:
  - TASK-316
priority: medium
project: whatsapp
ordinal: 331000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The model offers a manager contact only through a tool that is gated on the card level (a warning first, a concrete call rare). The model cannot name the number before the card reaches the level, and the candidate text never promises that a human will call (TASK-314 AC 15). Blocked on Ivan: the manager number, who answers, the hours. These are config only and never go into the repository. Requested by the WhatsApp harness lane (wa-harness) through pflege-clawl, 2026-10-07; source: Ivan 2026-10-07, notes already on TASK-314 and TASK-316 (branch docs/backlog-next-step-and-temperature). No candidate data in this text; German candidate wording only with Ivan's verbatim approval.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The tool refuses below the card level and the refusal is tested
- [ ] #2 The number comes from config only; no phone number appears in the repository, a fixture or a log
- [ ] #3 No candidate text promises a human call (TASK-314 AC 15), checked by a test over the templates and the prompt
- [ ] #4 Manager number, who answers and the hours are given by Ivan and configured on the VM
<!-- AC:END -->
