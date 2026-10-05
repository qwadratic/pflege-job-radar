---
id: TASK-393
title: >-
  Root-cause the multi-hour reply stall: ~40 brain calls over 2.5 h before one
  inbound got an answer
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
updated_date: '2026-10-05 17:58'
labels:
  - luna
  - latency
dependencies: []
references:
  - app/wa/luna/catchup.py
priority: high
type: bug
project: whatsapp
ordinal: 268000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the practice audit on 2026-09-29. On test thread B, the inbound at 2026-09-23 12:45:12 got its first reply at 15:22:32, 2h37m later.
- In between, wa_luna_calls logged ~40 invocations 2-8 min apart. That matches the catch-up poller retrying an owed thread over and over.
- TASK-412 (stuck_reply flag after 2 h) only makes the stall visible. TASK-410 (calls/hour cap) only caps its cost.
- Neither explains why 40 calls produced no reply.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The root cause of the 2026-09-23 12:45 stall is found from wa_luna_calls and the journal and written into the notes
- [ ] #2 Fixed with a failing-then-passing test, or closed with a written argument
- [ ] #3 An owed thread that keeps retrying without a reply raises a signal well before the 2 h stuck_reply flag
<!-- AC:END -->
