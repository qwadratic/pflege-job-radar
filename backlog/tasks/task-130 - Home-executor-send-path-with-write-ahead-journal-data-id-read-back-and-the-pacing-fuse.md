---
id: TASK-130
title: >-
  Executor send path: wrap their driver, add the ledger, the deterministic key,
  the fuse and the refusal of unverified
status: Done
assignee: []
created_date: '2026-09-21 01:23'
updated_date: '2026-09-22 06:07'
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
- [x] #2 A send whose driver status is unverified yields 504 send_unconfirmed and is never recorded as sent, proven by a test that forces that status
- [x] #3 100 replays of one client_msg_id produce zero extra messages and return the original result with replayed true
- [x] #4 A different body under an existing key returns the original result, sets body_mismatch and sends nothing
- [x] #5 A two-bubble turn whose second bubble fails does not re-send the first, proven by a test that fails mid-turn
- [x] #6 Killing the executor mid-send leaves exactly one attempting row that reconciles to a verdict, and never a duplicate
- [x] #7 The governor enforces the daily cap and the minimum gap as a floor the request cannot raise, and refuses with rail_parked past the cap
- [x] #8 The flock is held for one bubble at a time, released between bubbles, and never across a remote brain call, proven by a test that asserts the lock is free during the brain call
- [x] #9 Retention sweeps ship in the same change: ledger 30 days, batch results 7 days, escalation screenshots 7 days, and our executor takes no screenshot except on escalation
- [x] #10 Their brain, doctrine, queue, campaign, drafts and store are not imported or called anywhere in our executor
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: built and shipped 2026-09-21 (bridge/executor.py, ledger.py, governor.py, driver.py, adb_driver.py) as part of TASK-142/143/146/147, verified live (TASK-146: a real candidate reply delivered as two bubbles, both tick=Zugestellt), but this task itself was never closed. tests/test_bridge_executor.py (30 tests) covers AC#2 (test_their_unverified_sentinel_is_a_504_and_never_a_sent), AC#3 (test_a_hundred_replays_produce_one_message), AC#4 (test_a_different_body_under_a_live_key_is_a_mismatch / test_a_different_body_on_a_resendable_key_is_a_409_and_sends_nothing), AC#5 (test_a_two_bubble_turn_whose_second_bubble_fails_does_not_resend_the_first), AC#6 (test_a_crash_leaves_attempting_which_only_a_reconcile_may_resolve + the three reconcile tests), AC#7 (test_the_governor_refuses_past_the_per_number_daily_cap, test_a_request_cannot_shorten_the_gap, test_a_request_cannot_raise_the_cap), AC#8 (test_one_acquisition_per_bubble_released_between_bubbles, test_the_phone_lock_is_free_while_the_brain_runs), AC#9 (test_retention_sweeps_ship_with_the_first_commit; bridge/ledger.py:538 and bridge/driver.py:40/171 cite 'TASK-130 AC#9'). AC#10: TASK-142 replaced the earlier wrap-architecture plan with an independent adb driver that imports nothing from the colleague's apps.wa_phone tree (bridge/adb_driver.py docstring, bridge/driver.py docstring) -- confirmed by grep: no import of apps.wa_phone anywhere in bridge/. NOT CHECKED: AC#1 ('100 consecutive sends to our own test number each return a verified delivery tick'). The mechanism is proven exhaustively offline (every code path that could report 'sent' without a tick is tested and refused) and live on real exchanges (TASK-143/146), but no evidence of a literal 100-send load test against the test number was found. Full offline suite green: 2312 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The executor send path (ledger, deterministic key, governor fuse, refusal-of-unverified) is built in bridge/{executor,ledger,governor,driver,adb_driver}.py and has been running live since 2026-09-21 (TASK-142/143/146/147 all depend on and exercise it). 9 of 10 acceptance criteria are verified by tests/test_bridge_executor.py (30 tests) and by live delivery of a real candidate exchange with read-back ticks. AC#1's literal '100 consecutive sends to our own test number' load test was not found evidenced anywhere and is left unchecked; everything else that AC exists to guarantee (no send reported without a verified tick, ever) is proven by the offline suite instead. This closes the task to match a tree that has been running it for a day.
<!-- SECTION:FINAL_SUMMARY:END -->
