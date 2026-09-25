---
id: TASK-299
title: Per-candidate status drives every send path
status: To Do
assignee: []
created_date: '2026-09-25 00:01'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 252000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24 design session. Today the harness has exactly one behaviour per candidate, so the only way to stop talking to someone is to stop the whole rail, and the only way to flag a lead is to remember it. He wants one column on the candidate that the harness reads before it does anything: mute / pause / red / skip / green. His governing sentence was 'т. е. мы в любом случае отвечаем' -- the status changes WHAT we answer, never WHETHER, with mute the single exception. This is the keystone of the rest of the design: it is what makes the media acknowledgement honest (flag the lead internally, answer the person normally), what makes escalation internal instead of a promise, and what makes silence a deliberate state instead of an accident. The five statuses are his words: mute = say nothing; pause/confused = yellow, 'всё норм, приняли, скоро ответим'; red = qualified, needs an urgent manual close; skip = dodge the topic politely and keep moving along the card; green = normal.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A status column exists on the candidate row with exactly the five values mute, pause, red, skip, green, defaulting to green
- [ ] #2 The status is set by an operator or by an explicit coded rule, never inferred by the model
- [ ] #3 All five send paths read it before sending: the webhook reply path, catch-up, follow-ups, the broadcast runner, and the media acknowledgement
- [ ] #4 mute is the only status that sends nothing; every other status still produces a reply
- [ ] #5 pause sends a short holding answer that confirms receipt and promises no human
- [ ] #6 red keeps answering normally and additionally raises the thread for urgent manual close in the operator's own surface
- [ ] #7 skip answers without engaging the dodged topic and still carries the card's open question
- [ ] #8 One test per status per send path proves what is sent and what is suppressed
<!-- AC:END -->
