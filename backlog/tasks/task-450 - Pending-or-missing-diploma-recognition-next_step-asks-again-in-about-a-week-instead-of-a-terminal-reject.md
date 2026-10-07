---
id: TASK-450
title: >-
  Pending or missing diploma recognition: next_step asks again in about a week
  instead of a terminal reject
status: To Do
assignee: []
created_date: '2026-10-07 15:17'
labels:
  - dialog
dependencies:
  - TASK-449
priority: high
project: whatsapp
ordinal: 330000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Today not_placeable is terminal in followups.py and the locked reject text ends the thread. For a candidate whose diploma recognition is pending or not yet started, the card gets a next_step that asks again in about a week instead. The exact pending states to treat this way are to be confirmed with Ivan; any candidate wording needs Ivan's verbatim approval. Requested by the WhatsApp harness lane (wa-harness) through pflege-clawl, 2026-10-07; source: Ivan 2026-10-07, notes already on TASK-314 and TASK-316 (branch docs/backlog-next-step-and-temperature). No candidate data in this text; German candidate wording only with Ivan's verbatim approval.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The pending and missing recognition states that must not end the thread are confirmed with Ivan and listed in the task
- [ ] #2 A candidate in such a state gets next_step set to ask again in about a week, and no terminal reject text
- [ ] #3 Which states stay terminal is confirmed with Ivan, and every state has a test
- [ ] #4 Candidate wording is approved verbatim by Ivan before it ships
<!-- AC:END -->
