---
id: TASK-400
title: 'Operator voice notes: language detected and stored as text marked voice'
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
labels:
  - agent-notes
dependencies:
  - TASK-303
  - TASK-210
priority: low
type: feature
project: whatsapp
ordinal: 275000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-25: a voice note must also get its language detected and land in the DB like text, marked as voice.

TASK-210 built this for candidates. Nothing verifies that the operator-note pipeline (Russian dictation into agent_note_gate) uses the same transcript and marker path.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 An operator voice note goes through the transcript + voice-marker path and the language classifier, verified with a test
<!-- AC:END -->
