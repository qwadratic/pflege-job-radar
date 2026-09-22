---
id: TASK-134
title: >-
  Lane-ownership record: pin the driver-library boundary against the colleague's
  wa_phone package
status: Done
assignee: []
created_date: '2026-09-21 09:09'
updated_date: '2026-09-22 06:07'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-21-macmini-revision.md
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: docs
ordinal: 142000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Blocking. Replaces TASK-112 at the head of the phone-rail chain; nothing downstream may be written until this is settled.

decision-8 chose the architecture (Option A, WRAP): our executor imports the colleague ~/wa-phone-outreach/apps/wa_phone modules device.py, whatsapp.py and inbox.py as a DRIVER LIBRARY ONLY. His brain, doctrine, queue, campaign, drafts and store are dead code to us. Luna stays the brain on our VPS and data/wa.sqlite stays the source of truth.

What is not settled is the boundary itself, and the boundary is the contract every downstream task codes against. His signatures are the API: read them read-only over ssh and write them down in our tree, because that package is a disposable agent worktree (gitdir points into clinic-dispatcher/.git/worktrees/wa-phone-outreach, branch cursor/wa-phone-outreach-7972) and a checkout can swap those files under us.

Entry condition that belongs here because it is a sequencing problem, not a design one: two automated senders on one consumer WhatsApp account is the one failure neither side can observe from its own side. We do not start until his daemon is reply-only to his own test number -- never bare --auto-reply, never --send-first-touches.

Also record the fallback shape if he declines the wrap, so a no does not restart the project from zero.

Read-only investigation: /home/claude/plans/2026-09-21-macmini-revision.md sections 2 and 4.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The record names every function we call in device.py, whatsapp.py and inbox.py, with its signature as read off his tree, and the commit those signatures were read at
- [ ] #2 It names what we deliberately do not use -- brain, doctrine, queue, campaign, drafts, store -- and says for each what replaces it on our side
- [ ] #3 It states the entry condition: his daemon is reply-only to his own test number, never bare --auto-reply and never --send-first-touches, and how that is observed before we start
- [ ] #4 It states the flock protocol we will follow: one bubble per acquisition of huawei01.lock, released between bubbles, and never held across a remote brain call
- [ ] #5 It names the fallback if the colleague declines the wrap, so a no is a rescope and not a restart
- [ ] #6 Nothing is deployed, written or run on the remote machine by this task; every fact in it is read-only
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
CLOSED AS SUPERSEDED, 2026-09-22, not done as written. This task exists to document the boundary of a WRAP architecture (Option A, decision-8): import the colleague's device.py/whatsapp.py/inbox.py from ~/wa-phone-outreach/apps/wa_phone as a driver library. That architecture was abandoned before this task's own record was ever written: TASK-142 (created 2026-09-21 09:54, after this task's 09:09) shipped bridge/adb_driver.py instead -- 'our own adb driver for the handset. Stdlib only, and it imports nothing of theirs' (bridge/adb_driver.py docstring). bridge/driver.py's own docstring states the history: 'This file used to wrap apps.wa_phone out of a colleague's agent worktree on the mini. TASK-142 replaced that with our own adb driver... We own this rail end to end now and import nothing from that tree.' Confirmed by grep: no import of apps.wa_phone anywhere under bridge/ or app/wa/. docs/whatsapp.md:60 states it plainly: 'the earlier plan to wrap his package as a driver library was dropped because a git worktree remove would swap every signature under us.' There is no driver-library boundary left to pin -- the boundary this task was written to record was never built. What DID need recording (the flock-sharing protocol, the entry condition of not messaging two senders on one account) is covered instead by TASK-141's question list and by bridge/adb_driver.py's own docstring. No acceptance criterion is checked: none was verified, because the artifact they describe (a wrap boundary) does not exist.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Obsolete, not implemented as written. The WRAP architecture this task exists to document a boundary for was abandoned in favour of TASK-142's independent adb driver (bridge/adb_driver.py, stdlib only, imports nothing from the colleague's tree) before this task was ever worked. There is no driver-library import boundary left to pin. The one part of this task that still matters -- the shared-handset flock protocol and the entry condition of not running two senders on one account -- is covered by TASK-141 and by bridge/adb_driver.py's own docstring instead.
<!-- SECTION:FINAL_SUMMARY:END -->
