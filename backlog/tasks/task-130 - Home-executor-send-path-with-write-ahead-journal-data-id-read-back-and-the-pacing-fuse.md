---
id: TASK-130
title: >-
  Executor send path: wrap their driver, add the ledger, the deterministic key,
  the fuse and the refusal of unverified
status: To Do
assignee: []
created_date: '2026-09-21 01:23'
updated_date: '2026-09-21 09:13'
labels:
  - wa-transport
dependencies:
  - TASK-114
  - TASK-119
  - TASK-129
  - TASK-136
  - TASK-141
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 138000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M5, RESCOPED HARD by decision-8 (2026-09-21). The send path already exists and already sends; we are not building it.

~/wa-phone-outreach/apps/wa_phone drives the consumer WhatsApp app over USB adb against the real app: ADBKeyboard typing at 3.2 to 5.5 chars per second, chat-open by smsto: intent with a last-8-digits wrong-thread guard, a 30 s send-verify loop, and per-message delivery ticks scraped off the bubble status content-desc (whatsapp.py:27: Gesendet, Zugestellt, Gelesen). It has been cycling as a daemon since 2026-09-20 15:53. We import device.py, whatsapp.py and inbox.py as a driver library and nothing else (TASK-134).

WHAT WE BUILD, and it is all correctness they do not have:

1. THE LEDGER, first body wins. A later different body for the same key returns the ORIGINAL result with replayed true and records body_mismatch; it does not send. Before executing any key whose row is attempting or unknown, reconcile first. They have no ledger and no key.

2. THE DETERMINISTIC KEY (TASK-114). One bubble per deterministic key, so a mid-turn failure never re-sends a delivered bubble. Theirs requeues the WHOLE item: bubbles loop inside one item with no per-bubble marker, so a two-bubble reply whose second bubble fails re-sends the first.

3. THE GOVERNOR FUSE (TASK-127). Always slower than asked, never faster; refuses past its own daily cap; the cap is non-overridable by the request so a server bug cannot open it.

4. THE REFUSAL OF UNVERIFIED. This is the single most important line in the task. Their code logs "composer empty after send but bubble not matched -- treating as sent (unverified)" and returns a bubble with status unverified; the caller then stores fingerprint=None, and NULLs are unlimited in a SQLite UNIQUE column. It fired on 2 of 23 live sends: the candidate gets silence and the record says sent. At our boundary unverified is a 504, uncertain, never auto-resent, never sent.

DELETED FROM THE ACCEPTANCE: the data-id read-back. There is no WhatsApp Web, no DOM and no provider message id on this rail. The client_msg_id we mint is the only id an outbound message has, and the proof of delivery is the tick.

Honest limit, stated not hidden: a tick is readable at send time but cannot be correlated afterwards. If the executor dies between pressing send and reading the tick, the only reconcile is a body scan of the chat -- a heuristic dressed as a verdict, and a wrong confirmed_absent authorises a resend to a real candidate. Keep the scan window tight and surface indeterminate to a human rather than guessing.

FLOCK PROTOCOL: one bubble per acquisition of huawei01.lock, released between bubbles, and never held across a remote brain call. Their cycle is 15 s with a 5 s timeout; holding the lock across a 180 s Luna turn would lock them out, and "device error: phone lock busy" has already been observed once.

RETENTION in the first commit, not later: ledger 30 days, batch results 7 days, escalation screenshots 7 days. Their shots directory reached 91 MB after roughly 16 hours of ONE test conversation with no off switch. All executor state stays on the remote machine; our VPS root filesystem is at 98 percent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 100 consecutive sends to our own test number each return a verified delivery tick read at send time, and no send is reported as sent without one
- [ ] #2 A send whose driver status is unverified yields 504 send_unconfirmed and is never recorded as sent, proven by a test that forces that status
- [ ] #3 100 replays of one client_msg_id produce zero extra messages and return the original result with replayed true
- [ ] #4 A different body under an existing key returns the original result, sets body_mismatch and sends nothing
- [ ] #5 A two-bubble turn whose second bubble fails does not re-send the first, proven by a test that fails mid-turn
- [ ] #6 Killing the executor mid-send leaves exactly one attempting row that reconciles to a verdict, and never a duplicate
- [ ] #7 The governor enforces the daily cap and the minimum gap as a floor the request cannot raise, and refuses with rail_parked past the cap
- [ ] #8 The flock is held for one bubble at a time, released between bubbles, and never across a remote brain call, proven by a test that asserts the lock is free during the brain call
- [ ] #9 Retention sweeps ship in the same change: ledger 30 days, batch results 7 days, escalation screenshots 7 days, and our executor takes no screenshot except on escalation
- [ ] #10 Their brain, doctrine, queue, campaign, drafts and store are not imported or called anywhere in our executor
<!-- AC:END -->
