---
id: TASK-162
title: >-
  Revert tonight's active-hours override on the mini bridge
  (WA_BRIDGE_ACTIVE_HOURS_OVERRIDE)
status: To Do
assignee: []
created_date: '2026-09-22 18:58'
labels:
  - wa-transport
  - operational
dependencies: []
references:
  - bridge/server.py
  - bridge/governor.py
priority: high
ordinal: 170000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-22 ~21:00 Europe/Berlin: widen the phone rail's send window past the built-in 9-20 fuse for tonight's UAT broadcast only ('расширь окно... это рассылка тестовая, разрешаю'). Implemented as an explicit, opt-in env override (bridge/server.py::active_hours_override, unset by default -- the built-in 9-20 fuse in bridge/governor.py::MINI_FLOOR is untouched in code) rather than editing the fuse's own default, specifically so this is a one-line env change to undo, not a code revert. WA_BRIDGE_ACTIVE_HOURS_OVERRIDE was set in ~/pflege-wa-bridge/bridge.env on the mini and the bridge service restarted to pick it up. This task exists so the override is not still live days from now, silently widening the send window on every subsequent night.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 WA_BRIDGE_ACTIVE_HOURS_OVERRIDE is removed (or commented out) from ~/pflege-wa-bridge/bridge.env on the mini
- [ ] #2 pflege-wa-bridge.service on the mini has been restarted after removing it
- [ ] #3 GET /v1/health on the mini shows quota.active_hours back to [9, 20]
<!-- AC:END -->
