---
id: TASK-114
title: >-
  Turn-scoped deterministic client_msg_id for conversational replies and
  campaign attempts
status: To Do
assignee: []
created_date: '2026-09-21 01:20'
updated_date: '2026-09-21 09:14'
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
- [ ] #1 The conversational key is derived only from phone, turn_key, action and bubble index, so the same turn regenerated any number of times yields byte-identical keys
- [ ] #2 Campaign sends use the natural key wab.o.camp.<campaign_id>.<phone>.<attempt> and no random component appears anywhere in either derivation
- [ ] #3 A test simulating a send failure followed by a catch-up regeneration produces exactly one delivery at the transport, not two
- [ ] #4 A second call with the same key and a different body returns the original result with replayed true and body_mismatch true, and sends nothing
- [ ] #5 The extra-bubble residual is covered by a test that asserts the documented behaviour rather than suppressing it
- [ ] #6 The key derivation is unit-tested directly so a future change to the format is caught as a breaking change
<!-- AC:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:14
---
decision-8 (2026-09-21): KEEP, with the key source changed. There is no inbound wamid on the phone rail -- that stack has no message identifier of any kind -- so turn_key derives from the inbound id WE mint, not from a provider id. bubble_index becomes first-class rather than an implementation detail: one bubble per HTTP call and one bubble per flock acquisition, because their executor requeues a whole item and a two-bubble reply whose second bubble fails re-sends the first. The client_msg_id we mint is the only id an outbound message on that rail ever has.
---
<!-- COMMENTS:END -->
