---
id: TASK-458
title: >-
  Tester feedback inbox (wacli rail): Russian text and voice from the test
  numbers, one card
status: To Do
assignee: []
created_date: '2026-10-10 10:31'
labels:
  - whatsapp
  - wacli
  - agent-notes
dependencies: []
priority: high
type: feature
project: whatsapp
ordinal: 339000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-10: Russian text and Russian voice that the wacli testers send are not German and never reach the brain (app/wa/wacli/ingest.py stores them with meta.outside_brain=not_german). They are the testers' feedback on the bot. They need a stream of their own and a fixed place the fixing agent reads from. This card is that place: one pre-made card, appended to by the consumer of TASK-459; it is never closed by the consumer and never duplicated. Each entry is added by code, not by a model: a stable item key, the tester label (not the number), the time, the verbatim Russian, the English decoding, and the wa_messages id it came from. The agent that fixes and opens a PR picks entries up from here.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The card exists on main and its id is the one the consumer is configured with
- [ ] #2 Every entry carries an item key; the same message is never appended twice
- [ ] #3 An entry holds the verbatim Russian, the English decoding, the tester label and the wa_messages id, and no phone number
<!-- AC:END -->
