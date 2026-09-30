---
id: TASK-283
title: >-
  WhatsApp harness console: watch the rail, schedule tasks into its queue,
  nothing else
status: To Do
assignee: []
created_date: '2026-09-23 16:23'
updated_date: '2026-09-30 20:02'
labels: []
dependencies:
  - TASK-281
priority: high
project: whatsapp
ordinal: 230000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-23, describing what should exist instead of autopilot: a console for watching the WhatsApp harness work. His words for the shape of it: "можно сказать, WhatsApp клиент, но только без права на прямую запись, а вот только возможность там как-то планировать задачи и просто рендерить то, что по факту там в базе данных лежит".

This is the parent of that family. The subtasks under it are the screens and the API. The concept
gate is TASK-281 and it comes first -- Ivan asked to approve the shape before anything is built.

THE ONE RULE THAT DEFINES THIS THING: it may not write to a conversation. No compose box, no send,
no edit, no hand-made state change. The only action a human takes here is scheduling a task into the
phone-ops queue the rail already runs (bridge/dispatcher.py, the FIFO that bridge/executor.py and
bridge/operations.py verbs are dispatched from). Everything else is a rendering of what is already
stored. The reason is the whole point of the console: it is for debugging and monitoring, and a
surface that can also act is a surface you cannot trust as a record of what happened.

WHAT IT READS, and it is two stores on two machines:
  * VPS, data/wa.sqlite -- wa_threads, wa_messages, wa_documents, wa_ownership, wa_luna_calls,
    wa_send_failures, wa_message_statuses, wa_inbound_pending, wa_suppressions;
  * Mac mini, /home/cursorworker1/.local/share/pflege-wa-bridge/ledger.sqlite -- phone_ops,
    outbound, inbound, journal, broadcast_run/broadcast_item, plus the shots/ and recordings/
    directories the debug capture writes per op_id.

WHERE IT RUNS (Ivan, 2026-09-23): API on the VPS tasker-dispatcher-01, reading the mini over the
link that already exists; frontend on the same domain as the board frontend,
https://pflege-board.exe.xyz.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every subtask under this one is either delivered or explicitly deferred in TASK-281 concept
- [ ] #2 The console has no HTTP route that can send, edit or delete a WhatsApp message, and a test asserts that
- [ ] #3 The only write path reaching the rail is the task-scheduling one from its own subtask
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Ivan, 2026-09-30, amends THE ONE RULE above for the Pro view (the board frontend, pflege-fe): "когда человек написал в про, то это уходит в очередь, и видно статус, что сообщение в очереди, и вообще видно статус этой очереди, как она разгребается в реальном времени, чтобы в случае чего сразу можно было вмешаться".
- A human message typed in Pro is ALLOWED. It is a queued send task, never a direct send, and it joins the same phone_ops FIFO that Luna uses.
- Luna is NOT paused when a human writes (Ivan explicitly rejected the pause).
- The message shows its queue state (queued/running/sent/failed). The whole queue is visible live as it drains, so a human can intervene at once.
- Also asked the same day: a "refresh" task (re-read the chat from the phone into SQLite, engine-side; the brain still never sees the screen), and a visible last-update time. Reads come from SQLite only; the engine keeps SQLite current.
- Delivery path: harness /api/wa/pro/* (TASK-395) plus the board proxy, not a separate console app.
<!-- SECTION:NOTES:END -->
