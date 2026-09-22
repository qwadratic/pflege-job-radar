---
id: TASK-134
title: >-
  Lane-ownership record: pin the driver-library boundary against the colleague's
  wa_phone package
status: To Do
assignee: []
created_date: '2026-09-21 09:09'
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
