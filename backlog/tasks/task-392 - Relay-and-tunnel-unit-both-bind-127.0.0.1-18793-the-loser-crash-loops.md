---
id: TASK-392
title: 'Relay and tunnel unit both bind 127.0.0.1:18793; the loser crash-loops'
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
labels:
  - rail
  - reliability
dependencies: []
references:
  - bridge/relay_pull.py
  - deploy/wa-bridge/pflege-wa-bridge-tunnel.service
priority: medium
type: bug
project: whatsapp
ordinal: 267000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Since 2026-09-29 18:13 UTC, bridge/relay_pull.py's own `ssh -L 127.0.0.1:18793` child holds the port. The trigger was a bridge restart on the mini after a deploy.
- pflege-wa-bridge-tunnel.service fails with "Address already in use" and restarts every ~5 s: ~550 journal lines/hour, 2569 by 22:51.
- The rail still works through the relay's forward, but only while the relay lives.
- The churn fills journald, which is the colleague's.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Exactly one process owns the forward: the relay reuses a forward that already answers, or each side gets its own port
- [ ] #2 A forward conflict never turns into a restart loop
- [ ] #3 /v1/health or bridge-health shows which process owns the forward
<!-- AC:END -->
