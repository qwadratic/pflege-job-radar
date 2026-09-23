---
id: TASK-236
title: >-
  A retried turn whose reply is reworded wedges on first-body-wins and can never
  be delivered
status: In Progress
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-23 09:17'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 183000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/executor.py:854. Severity: loses-messages. 

HOW IT HAPPENS: A send fails in a way that leaves the key resendable — open_chat raises, so mark_not_attempted writes NOT_ATTEMPTED; or a reconcile marks it ABSENT. Catch-up re-drives the turn through process_owed_turn, which calls LB.turn() again, and bubble 0 comes back phrased differently. The key is identical, body_sha256 differs, ledger.classify returns 'mismatch', and because the state is RESENDABLE, _mismatch_response raises idempotency_conflict instead of sending. The next pass regenerates, mismatches, and so on.

WHAT IT COSTS: A message that was genuinely never typed on the handset becomes permanently undeliverable on that turn_key. The candidate gets silence; wa_send_failures accumulates identical rows; the retry loop keeps paying for brain calls and eating that phone's hourly Luna budget.

PROPOSED DIRECTION (not a decision): Decide which artefact is authoritative for a retry. Either persist the bubbles of a failed turn when they are generated and replay them verbatim rather than regenerating, or let a resendable key with a new body supersede the old one (NOT_ATTEMPTED and ABSENT both mean the old body demonstrably never reached the phone), recording the mismatch rather than refusing on it. A body-sensitive check plus a non-deterministic generator has no exit.

VERIFICATION NOTES: CONFIRMED. ledger.classify (ledger.py:426-433) compares body_sha256 BEFORE it looks at state, so a reworded bubble under a live key always returns 'mismatch'; _mismatch_response (executor.py:848-856) then raises idempotency_conflict precisely when the state IS resendable. mark_not_attempted writes NOT_ATTEMPTED (executor.py:167, on the open_chat failure path) and NOT_ATTEMPTED/ABSENT are exactly RESENDABLE (ledger.py:52). reply_key (bridge_ids.py:96) hashes phone|turn_key|action|bubble_index with no body component, so the key is stable while the body is not, and LB.turn is a live Sonnet call — non-determinism is the normal case, not a pathology. No attempt cap exists anywhere in catchup.py or drain_pending, so it regenerates and conflicts on every pass. bridge_ids.py:36-38 admits only the opposite residual (a regenerated reply with MORE bubbles sending a fragment); this direction is stated nowhere. Same brain-budget starvation as the previous finding applies.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed. Sceptic's diagnosis confirmed by reading the code directly: ledger.classify()
(ledger.py:446-465, pre-fix) compared body_sha256 before consulting state and returned
'mismatch' unconditionally on a difference; executor._mismatch_response (executor.py:898-906)
then raised idempotency_conflict specifically because entry.state was in RESENDABLE. Rule 3's
own stated justification ("a regenerated reply must never overwrite a delivered one") is about
protecting a SENT/in-flight body -- NOT_ATTEMPTED/ABSENT mean the opposite by construction (the
old body never reached the phone), so refusing there had no delivered body to protect and just
wedged the turn_key forever against catch-up's non-deterministic LB.turn() regeneration.

Two coordinated edits in bridge/ledger.py, matching the sceptic's sketch:
1. Ledger.classify(): still records the body_mismatch row (audit trail kept), but only returns
   'mismatch' when entry.state is NOT in RESENDABLE. A RESENDABLE mismatch now falls through to
   'proceed'.
2. Ledger.begin()'s ON CONFLICT clause now also refreshes body_sha256/body_len (previously only
   state/attempts/attempted_at/resolved_at/detail/tick/tick_state/bubble_clock were refreshed).
   Without this, a RESENDABLE key proceeding with a new body would leave the row's hash pointing
   at the stale first body while the new body is what actually got typed -- corrupting the
   "body_sha256 records what was sent" invariant and misclassifying the next honest replay of the
   body that actually sent as a fresh mismatch. Also updated the module's own FOUR RULES docstring
   (Rule 3) and both functions' docstrings so they state the RESENDABLE carve-out plainly rather
   than leaving the old, now-inaccurate "first body wins, always, whatever the key's state" claim
   in place.

Left executor.py's _mismatch_response() untouched: its `if entry.state in L.RESENDABLE: raise
idempotency_conflict` branch is now unreachable (classify() never returns 'mismatch' for a
RESENDABLE entry any more), but it is harmless -- not incorrect for the call it would receive --
and touching it is outside this task's blast radius per the sceptic's own scoping (ledger.py's
classify()/begin() only, not the send/lock/driver/executor path).

Test: rewrote the pinned test tests/test_bridge_executor.py::
test_a_different_body_on_a_resendable_key_is_a_409_and_sends_nothing into
test_a_different_body_on_a_resendable_key_proceeds_and_sends (second body on a NOT_ATTEMPTED key
now sends 200/sent instead of 409, and the mismatch is still counted for audit). Added
test_a_resent_new_body_updates_the_stored_hash_so_its_own_replay_is_a_replay, proving begin() now
stores the new body's hash so a later replay of that same new body classifies as 'replay', not
another mismatch -- this is the case a naive classify()-only fix would have silently broken.
Verified both fail (409/stale-hash) on the pre-fix code and pass after.

Ran tests/test_bridge_executor.py only, per instructions: 135 passed. Did not run the full
suite (that is the separate verification pass). Not committed -- diff is in the working tree in
bridge/ledger.py and tests/test_bridge_executor.py.
<!-- SECTION:NOTES:END -->
