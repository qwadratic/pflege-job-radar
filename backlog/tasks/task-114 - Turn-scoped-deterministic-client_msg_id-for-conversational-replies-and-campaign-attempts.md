---
id: TASK-114
title: >-
  Turn-scoped deterministic client_msg_id for conversational replies and
  campaign attempts
status: Done
assignee: []
created_date: '2026-09-21 01:20'
updated_date: '2026-09-22 06:07'
labels:
  - wa-transport
dependencies:
  - TASK-120
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 122000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M5, and the single most important engineering decision in the plan.

Verified failure chain today: `_send` raises -> `send_and_record` records `wa_send_failures` and re-raises (`app/wa/api.py:937-946`) -> the caller sets the reply-turn claim to `skipped_error` (`api.py:866`) -> `app/wa/store.py:302` documents `skipped_error` as reclaimable -> `pflege-wa-catchup.timer` re-drives `process_owed_turn` three minutes later -> the brain runs again -> NEW bubbles with a fresh random key -> the home-machine ledger cannot match them -> the candidate is messaged twice.

On the Meta rail the unconfirmed window is small. On a phone-mediated send it is 20-60 seconds and is the DOMINANT failure class, not an edge case. A duplicate to a real candidate is unrecoverable and reads as a bot.

Fix: the idempotency key is turn-scoped and deterministic, not per-call random.
  client_msg_id = "wab.o." + sha256(f"{phone}|{turn_key}|{action}|{bubble_index}")[:32]
`turn_key` is the inbound wamid already used for the reply-turn claim (`api.py:691`, `store.py:297`). Campaign sends use the natural key wab.o.camp.<campaign_id>.<phone>.<attempt> from data `campaign.py` already holds, which gives idempotency across process death and is strictly better than what Meta gives us today.

Known residual, stated not hidden: if a regenerated reply has MORE bubbles than the first attempt, the extra index is a new key and sends, so the candidate sees a fragment. Rare, additive, accepted; it is measured, not guarded against.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The conversational key is derived only from phone, turn_key, action and bubble index, so the same turn regenerated any number of times yields byte-identical keys
- [x] #2 Campaign sends use the natural key wab.o.camp.<campaign_id>.<phone>.<attempt> and no random component appears anywhere in either derivation
- [x] #3 A test simulating a send failure followed by a catch-up regeneration produces exactly one delivery at the transport, not two
- [x] #4 A second call with the same key and a different body returns the original result with replayed true and body_mismatch true, and sends nothing
- [x] #5 The extra-bubble residual is covered by a test that asserts the documented behaviour rather than suppressing it
- [x] #6 The key derivation is unit-tested directly so a future change to the format is caught as a breaking change
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: this shipped since the task text was last touched, in app/wa/bridge_ids.py (reply_key/campaign_key, require_e164/require_text/require_index), consumed from app/wa/bridge.py Client.begin_turn/begin_campaign_attempt and cited at app/wa/api.py:997. Tests: tests/test_wa_bridge_ids.py (24 tests: same-turn same-process key stability, wab.o. + 32 hex, one key per bubble, every component changing the key, turn_key required with no default, bad turn_key/phone/bubble_index raising, campaign key spelled out literally, a retried campaign attempt keeping its key, module purity) plus tests/test_wa_bridge_client.py::test_the_bubbles_of_one_turn_are_separate_calls_with_separate_keys, ::test_a_regenerated_turn_re_posts_the_same_key_for_a_bubble_that_already_went_out (AC#3/#5 residual), ::test_a_different_body_under_a_live_key_is_a_mismatch_and_sends_nothing / bridge/ledger.py first-body-wins (AC#4). Full offline suite green: 2312 passed. This task's own dependency chain (TASK-120) is also done -- see that task.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:14
---
decision-8 (2026-09-21): KEEP, with the key source changed. There is no inbound wamid on the phone rail -- that stack has no message identifier of any kind -- so turn_key derives from the inbound id WE mint, not from a provider id. bubble_index becomes first-class rather than an implementation detail: one bubble per HTTP call and one bubble per flock acquisition, because their executor requeues a whole item and a two-bubble reply whose second bubble fails re-sends the first. The client_msg_id we mint is the only id an outbound message on that rail ever has.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Turn-scoped deterministic client_msg_id shipped in app/wa/bridge_ids.py: reply_key(phone,turn_key,action,bubble_index) and campaign_key(campaign_id,phone,attempt), both pure functions with explicit required arguments (no silent defaults) validated by tests/test_wa_bridge_ids.py. Wired into app/wa/bridge.Client (begin_turn/begin_campaign_attempt) and enforced end to end by bridge/ledger.py's first-body-wins. Verified by tests/test_wa_bridge_ids.py (24), tests/test_wa_bridge_client.py's turn/replay/body-mismatch tests, and a green full offline suite (2312 passed). Landed as part of the phone-rail work on 2026-09-21 but never marked; this closes the task to match the tree.
<!-- SECTION:FINAL_SUMMARY:END -->
