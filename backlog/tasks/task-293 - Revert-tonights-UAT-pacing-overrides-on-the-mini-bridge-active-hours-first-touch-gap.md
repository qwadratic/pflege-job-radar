---
id: TASK-293
title: >-
  Revert tonight's UAT pacing overrides on the mini bridge (active hours +
  first-touch gap)
status: To Do
assignee: []
created_date: '2026-09-24 01:59'
labels:
  - whatsapp
  - operational
  - anti-ban
dependencies: []
ordinal: 246000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24 ~02:00 UTC, live UAT: asked to confirm there is no anti-ban throttling in effect and that the governor is not choking frequent sends -- 'для uat это точно ок' (explicitly fine for UAT, not a standing policy change). Set WA_BRIDGE_ACTIVE_HOURS_OVERRIDE=0-24 (built-in fuse is 9-20 Europe/Berlin, and per governor.py's own TASK-356 hole #2 fix this gates every outbound including replies, not just first touches -- it was closed at the time of this request) and WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC=60-90 (built-in floor is 240-600s/4-10min; 60-90s is the same value Ivan approved for the 2026-09-23 UAT round) in ~/pflege-wa-bridge/bridge.env on the mini, restarted pflege-wa-bridge.service. Same precedent and same eventual fate as TASK-391 (2026-09-22's active-hours override), which sat live a full day past its stated one-night lifespan before being noticed and reverted -- this task exists specifically so that does not happen again.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 WA_BRIDGE_ACTIVE_HOURS_OVERRIDE is removed (or commented out) from ~/pflege-wa-bridge/bridge.env on the mini
- [ ] #2 WA_BRIDGE_FIRST_TOUCH_GAP_OVERRIDE_SEC is removed (or commented out) from ~/pflege-wa-bridge/bridge.env on the mini
- [ ] #3 pflege-wa-bridge.service on the mini has been restarted after removing both
- [ ] #4 GET /v1/health on the mini shows quota.active_hours back to [9, 20]
<!-- AC:END -->
