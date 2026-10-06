---
id: TASK-283.2
title: 'Console: thread list and full message history, read-only'
status: To Do
assignee: []
created_date: '2026-09-23 16:23'
labels: []
dependencies:
  - TASK-283.1
parent_task_id: TASK-283
priority: high
project: whatsapp
ordinal: 232000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan: "история всех сообщений... можно сказать, WhatsApp клиент, но только без права на прямую запись". The screen everyone will actually live in.

Today the only way to read a conversation is sqlite3 against data/wa.sqlite on the VPS, or opening
the chat on the handset itself -- and opening it on the handset is not free: it clears the
notification shade, which is one of the inbound doors (bridge/watcher.py::InboundWatcher). A person
looking at a conversation should not be able to damage its capture, and today they can.

What the screen has to carry beyond the bubbles, because this is a debugging surface and not a
messenger: which rail each thread is pinned to (wa_threads.rail: bridge or meta), ownership
(wa_ownership), whether it is a test thread, the delivery outcome of every outbound bubble from the
mini ledger rather than from our own optimism, attached documents (wa_documents) and inbound media,
and the send failures (wa_send_failures) that never became a bubble at all.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A thread list shows every conversation with its rail, ownership, last inbound and last outbound
- [ ] #2 Opening a conversation in the console never touches the handset
- [ ] #3 Each outbound bubble shows its real ledger state, and UNCONFIRMED is visibly different from SENT rather than both reading as delivered
- [ ] #4 Inbound media and documents are visible in place in the conversation
- [ ] #5 A send that failed before it became a bubble is visible in the thread rather than missing from it
<!-- AC:END -->
