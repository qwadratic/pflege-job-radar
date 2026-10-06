---
id: TASK-440
title: >-
  Bridge stays up 24/7 and touches the handset only in its own time window,
  using a shared phone lock path
status: To Do
assignee:
  - wa-harness
created_date: '2026-10-06 10:30'
labels:
  - whatsapp
  - wa-transport
dependencies: []
priority: high
ordinal: 317000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06: the handset on the Mac mini is shared with Dobby through fixed time windows. pflege has the daytime window; Dobby's operator sets Dobby's hours in /var/lib/handset/phone-windows.json (tz Europe/Vienna). Our bridge (pflege-wa-bridge) is never stopped. Outside its window it does not touch the phone: the watchers, reconcile, dispatcher and sends all pause, /v1/health stays up and says "paused until HH:MM", and a send gets a loud 503 rail_paused with next_slot_at, which the harness treats like rail_parked. The phone lock path moves from ~/.local/share/wa_phone/huawei01.lock (per user) to a configurable shared path (/var/lib/handset/huawei01.lock, group handset), because Dobby, pflege and the colleague's wa_phone lane run as different users. Requested by the WhatsApp lane (wa-harness).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The window is read from the file on every cycle, with no restart needed; a missing or bad file is a loud health error, never a guess
- [ ] #2 Outside the window there is no adb call at all (offline test with a fake driver)
- [ ] #3 The lock path comes from env WA_BRIDGE_PHONE_LOCK, with no silent default to the old path once it is set
- [ ] #4 The harness on the VPS shows "rail paused" and its catch-up sends after the window opens
- [ ] #5 docs and INSTALL.md are updated
<!-- AC:END -->
