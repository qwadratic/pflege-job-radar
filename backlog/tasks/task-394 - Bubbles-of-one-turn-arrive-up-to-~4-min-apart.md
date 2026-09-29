---
id: TASK-394
title: Bubbles of one turn arrive up to ~4 min apart
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
labels:
  - rail
  - latency
dependencies: []
references:
  - app/wa/api.py
priority: medium
type: bug
project: whatsapp
ordinal: 269000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Test thread A:
- the two bubbles of one turn went out 3m53s apart (10:55:07 and 10:59:00);
- a three-bubble warming turn went out ~1 min between bubbles.

There is no deliberate delay in the send loop. The time is spent inside each send_text call, in the rail transport. The candidate sees a bare acknowledgement, then waits minutes for the question.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Per-bubble send duration is measured from the mini ledger (phone_ops started/finished) for recent turns, and the notes name where the time goes
- [ ] #2 Bubbles of one turn go out within a target set from that measurement, or Ivan accepts the explained gap
<!-- AC:END -->
