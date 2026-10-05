---
id: TASK-345.12.15
title: Desk status check also catches a batch process that died without a halt event
status: To Do
assignee: []
created_date: '2026-10-05 12:28'
labels: []
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 297000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05, answering the open point of TASK-345.12.14: a batch process killed with SIGKILL or by the out-of-memory killer, or lost with its tmux window, writes no halt event, so the status check sees nothing and no letter goes out at its time. Before 05.10 only a clean exit (error, SIGTERM, SIGHUP) was logged. The check must see that an approved batch with letters left has no process behind it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every running batch process stamps a liveness file next to its batch (time and pid) at each poll, also while it waits for the announcement
- [ ] #2 The desk check mails the notify list once when an approved batch has letters left that no later batch carries, no halt in force, and a liveness stamp older than a threshold in the desk config (or none at all while its first send time is near); the mail names the batch, the last stamp and the command that starts it again
- [ ] #3 A batch that ended ("рассылка завершена"), was halted, or is carried by a later plan is never mailed, and a process that restarts inside the threshold is no news
- [ ] #4 tests cover: a fresh stamp sends nothing, a stale stamp mails once, an ended batch and a superseded batch send nothing
<!-- AC:END -->
