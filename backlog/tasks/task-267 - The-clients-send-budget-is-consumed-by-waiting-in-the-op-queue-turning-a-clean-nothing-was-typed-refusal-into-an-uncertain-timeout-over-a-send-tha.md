---
id: TASK-267
title: >-
  The client's send budget is consumed by waiting in the op queue, turning a
  clean "nothing was typed" refusal into an uncertain timeout over a send tha
status: To Do
assignee: []
created_date: '2026-09-23 08:03'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 214000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/bridge.py:473. Severity: degraded. 

HOW IT HAPPENS: A Luna bubble is queued behind a delete (budgeted 222 s), a captioned gallery, or a multi-id reconcile. It is still QUEUED when its own ~90 s budget expires; the client raises BridgeError(504, answer_timeout); send_and_record records the failure and api.py:885 finishes the claim skipped_error; the dispatcher then runs the op and types the message into the candidate's chat, unsupervised, minutes after the VPS gave up on it.

WHAT IT COSTS: A send whose outcome used to be certain and clean is now uncertain, and the VPS's record of the turn is written before the send happens. Catch-up re-drives on the strength of that failure -- which is where findings 1 and 2 cash in and produce a duplicate or a phantom record.

PROPOSED DIRECTION (not a decision): The wait for the phone and the work on the phone are two budgets and should be two numbers: wait a bounded, explicit time for the op to START (the old 30 s flock semantics, answered as a retryable refusal on expiry) and only then start spending the route's execution budget. phone_ops already records position and started_at, so GET /v1/ops/<id> can tell the client "not begun" from "in progress" instead of leaving it to guess from one clock.

VERIFICATION NOTES: CONFIRMED. _request computes `budget = self.timeout if timeout is None else timeout` and passes that SAME budget to _await_op (bridge.py:470-473), whose deadline starts at the moment the 200-queued answer comes back, i.e. at enqueue. For a send the budget is send_timeout(body) = max(90, 90 + len(body)/3.2) (bridge.py:439) -- a figure whose 90 s fixed term is itself the measured cost of ONE bubble's execution (bridge.py:136-139), leaving essentially zero slack for queue wait. claim_next_op (ledger.py:547) is FIFO across every kind with no expiry and no cancellation, so after the client gives up the dispatcher still runs the op and types the message. The prior behaviour the finder cites is right: take_phone (executor.py:368) refuses with device_unavailable after LOCK_TIMEOUT_SEC=30 (driver.py:38), before ledger.begin, leaving no row and a clean key. Worth naming a concrete pairing: a show_clinic_photos gallery op (finding 3, 140-220 s) sits in the same FIFO ahead of the very reply bubbles the same turn is about to send.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->
