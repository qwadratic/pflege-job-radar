---
id: TASK-294
title: >-
  Monitor the reverse SSH tunnel to the phone-rail mini and alert on a prolonged
  outage
status: In Progress
assignee: []
created_date: '2026-09-24 08:28'
labels:
  - whatsapp
  - operational
  - reliability
dependencies: []
ordinal: 247000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live incident, 2026-09-24 01:07-01:44 UTC: the mini's reverse SSH tunnel (127.0.0.1:2222 on tasker-dispatcher-01, ssh config Host macmini -- both pflege-wa-bridge-tunnel.service and any manual ssh/scp to the mini go through this one channel) dropped for 36 minutes with zero alert anywhere; only noticed via an ad-hoc manual check. pflege-wa-bridge-tunnel.service's own systemd unit just retries silently on ExitOnForwardFailure -- there is no visibility into how long it has been down. Ivan, 2026-09-24: file a small task for this and do it now.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A lightweight check (systemd timer or an addition to an existing periodic job) detects whether 127.0.0.1:2222 is accepting connections
- [ ] #2 A prolonged outage (threshold, e.g. 60s+) is logged loudly (journalctl) so it is visible without an ad-hoc manual check -- matches CLAUDE.md's 'failures fail loudly and get recorded'
- [ ] #3 The check itself never touches the mini or the bridge -- a plain TCP connect to the local forwarded port, so it cannot itself contribute load or false-trigger the phone
<!-- AC:END -->
