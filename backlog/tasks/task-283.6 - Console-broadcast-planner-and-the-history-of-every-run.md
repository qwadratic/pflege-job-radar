---
id: TASK-283.6
title: 'Console: broadcast planner and the history of every run'
status: To Do
assignee: []
created_date: '2026-09-23 16:24'
labels: []
dependencies:
  - TASK-283.3
parent_task_id: TASK-283
priority: medium
project: whatsapp
ordinal: 236000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan: "и планировщик рассылок, то есть история всех сообщений".

A broadcast today is a CLI act: tools/wa_bridge.py broadcast --id --file recipients.csv, which plans
and prints by default and only queues with --send. The executor side already has the state machine
and the books -- bridge/broadcast.py with broadcast_run and broadcast_item, RUN_OPEN/STOPPED/DONE,
ITEM_QUEUED/SENT/REFUSED/FAILED, a position column for strict order, and a runner thread that paces
items and steps aside whenever the phone-ops queue is busy. There is also a stop that takes effect
between items. None of that is visible to anyone who is not on a terminal with the tunnel up.

So this task is mostly surfacing what exists, plus the planning half: choosing recipients, writing
the message, seeing the plan before it runs, and watching it go. Note what the phone rail cannot do,
so the planner does not promise it: there are no tappable buttons and no approved templates on a
consumer handset, so an opening message is plain text and the answer comes back as words
(app/wa/luna/choices.py recovers a typed "ja"). Suppressions (wa_suppressions) and the per-number
daily cap on the mini (WA_BRIDGE_PER_NUMBER_DAILY_CAP) both constrain who can actually be reached.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every past and current broadcast run is visible with its per-recipient outcome
- [ ] #2 A run can be planned and reviewed in full before anything is queued
- [ ] #3 The planner shows, before the run starts, who will be skipped and why (suppression, cap, ownership)
- [ ] #4 A running broadcast can be stopped from the console, and the stop takes effect between items the way the executor already does it
- [ ] #5 The planner never claims a capability the phone rail does not have, such as buttons or templates
<!-- AC:END -->
