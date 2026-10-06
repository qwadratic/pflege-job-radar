---
id: TASK-283.3
title: 'Console: phone-ops queue view, and scheduling your own task into it'
status: To Do
assignee: []
created_date: '2026-09-23 16:23'
updated_date: '2026-09-30 20:02'
labels: []
dependencies:
  - TASK-283.1
parent_task_id: TASK-283
priority: high
project: whatsapp
ordinal: 233000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan: "единственное, как можно вмешаться, это запланировать задачи в этот таскер, то есть в ту же самую очередь, свои собственные задачи". This is the one write path the console has, and the queue view it needs to be usable.

The queue already exists and is the rails own: bridge/dispatcher.py claims the oldest queued
phone_ops row in strict position order and runs it; states are queued/running/done/failed; every row
carries kind, args, result or error, a budget, and the timestamps. Several background watchers put
work in it too (UnresolvedSendWatcher, ReconcileWatcher, the broadcast runner), so a human scheduling
a task is joining an existing stream, not starting one.

The thing to be careful about, and it is why this is its own task rather than a button on another
screen: the queue is the single-file path to a real phone that also carries real candidate
conversations. A human-scheduled task competes with them for the handset lock and for queue position.
The concept in TASK-281 has to settle which kinds a human may enqueue; the obvious safe set is the
read-only verbs (read a thread, list chats, look at the screen, reconcile a send) and the obvious
dangerous ones are send, clear-chat and delete-chat -- clear and delete already demand --confirm and
an expected message count on the CLI (tools/wa_bridge.py) precisely because they destroy.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The queue is visible live: every row with kind, state, position, age, budget and the result or error it ended with
- [ ] #2 A human can schedule a task of an allowed kind and watch it run to a terminal state
- [ ] #3 The allowed kinds are an explicit list, and a request for any other kind is refused with the reason
- [ ] #4 A human-scheduled task is distinguishable in the queue and in the ledger journal from one a watcher enqueued
- [ ] #5 Scheduling never bypasses the FIFO: a human task waits its turn like every other row
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Ivan, 2026-09-30 (see the TASK-283 notes): send IS an allowed human kind from Pro, as a queued task with a visible state; no Luna pause; the live queue view is the intervention point. The queue view and task status go through the Pro API (TASK-395 harness routes plus the board proxy, owner-gated).
<!-- SECTION:NOTES:END -->
