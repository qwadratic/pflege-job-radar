---
id: TASK-245
title: >-
  The reply key is keyed on the model's own action slug, so a catch-up re-drive
  can re-key the whole turn and deliver it twice
status: In Progress
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-23 10:16'
labels:
  - rail-critique
  - wrong-answer-to-candidate
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 192000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/bridge_ids.py:96. Severity: wrong-answer-to-candidate. 

HOW IT HAPPENS: Luna answers inbound M with two bubbles, action "propose_matches". Bubble 0 sends and ticks (ledger SENT). Bubble 1 raises (429 min-gap, per-number cap, 503 busy flock, or the 504 of finding 7). api.py:885 finishes the claim skipped_error. Three minutes later catch-up re-drives the same turn_key; the resumed session now answers with action "ask_housing" (or the harness forces "reply_after_correction"). Both bubbles get brand-new client_msg_ids; the executor classifies "proceed" and sends them.

WHAT IT COSTS: The candidate receives the reply twice -- the first attempt's bubble 0 plus a complete second answer, possibly worded differently. First-body-wins never fires because the keys do not collide. Every governor cap is spent twice.

PROPOSED DIRECTION (not a decision): Drop `action` from reply_key's material: turn_key already scopes the turn and bubble_index already scopes the bubble, so nothing is lost. If the action must stay, pin it -- record the action chosen on the first attempt next to the reply-turn claim (wa_reply_turn_claims already exists per phone+turn_key) and have the re-drive reuse that value instead of whatever the second brain call returns.

VERIFICATION NOTES: CONFIRMED, and stronger than the finder states. reply_key's material is literally `f"{phone}|{turn_key}|{action}|{bubble_index}"` (bridge_ids.py:96) and `action` is handed in by api._send at app/wa/api.py:1021 (`cl.begin_turn(t["phone"], turn_key, action)`) from `d["action"]`, which luna_brain.py:1383/1437 takes straight off `out.get("action")` -- and OUTPUT_SCHEMA declares it `{"type": "string"}` with NO enum (luna_brain.py:704). prompts.py:640 only *suggests* a vocabulary; nothing validates it. Two harness paths rewrite it outright: luna_brain.py:1204 forces "reply_after_correction" whenever the grounding check made the model retry, and luna_brain.py:1466 forces "test_source_links". The claim chain holds: send_and_record re-raises, api.py:885 finishes the claim `skipped_error`, store.py:374-376 documents skipped_error as reclaimable, deploy/pflege-wa-catchup.timer re-drives every 3 min, and process_owed_turn calls the brain again with the same turn_key. A drifted action gives every bubble a key ledger.classify (ledger.py:423) has never seen -> "proceed" -> the executor types the whole reply again. The module docstring's own promise ("a pure function of the turn: same phone, same inbound message, same action, same bubble index") quietly smuggles a model-chosen free-text field into what it calls a pure function.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed. app/wa/bridge_ids.py::reply_key no longer hashes `action` -- material is now
f"{phone}|{turn_key}|{bubble_index}". `action` stays a required, validated kwarg (bridge.py's
_next_send still needs it for trace["intent"], the governor's pacing field); only its contribution
to the hash is gone. Module + function docstrings updated: the old "same action" line in the pure-
function claim was false (action is the model's own free-text per-call output, no enum on
OUTPUT_SCHEMA, and two harness paths in luna_brain.py force it outright), replaced with an
explanation of why action is deliberately excluded.

Verified the blast radius before touching anything: reply_key has exactly one call site outside
tests (app/wa/bridge.py:424), so no changes needed to api.py, bridge.py's begin_turn signature,
ledger.py, or the wa_reply_turn_claims claim chain, as expected.

Tests:
- tests/test_wa_bridge_ids.py: flipped the {"action": "media_ack"} case out of
  test_every_component_changes_the_key (that assertion is exactly the bug) and added
  test_action_does_not_change_the_key asserting the opposite, with a comment pointing at TASK-245.
- tests/test_wa_bridge_rail_end_to_end.py: added
  test_a_catch_up_re_drive_with_a_different_action_slug_still_replays_instead_of_resending -- same
  turn_key driven twice 180s apart (mimicking the catch-up timer) with two different action strings
  ("propose_matches" then "ask_housing"). Confirmed by hand (temporarily reverting just
  bridge_ids.py) that this reproduces real double delivery on the old code: rail.driver.sent held
  the same reply twice, not just a key mismatch. Passes with the fix: one send.

Ran only the narrow files: `.venv/bin/python -m pytest tests/test_wa_bridge_ids.py
tests/test_wa_bridge_rail_end_to_end.py -q` -> 40 passed. Also ran
tests/test_wa_bridge_client.py + tests/test_wa_bridge_cli.py since they call reply_key too (all
existing calls use action="reply" consistently on both sides of each comparison, so unaffected by
dropping action from the hash) -- 139 passed, 1 pre-existing failure
(test_a_send_keeps_the_budget_it_had) that reproduces identically with bridge_ids.py fully reverted,
i.e. unrelated to this change, coming from other uncommitted work already in this tree (media-send
timeout math, not reply_key). Left untouched, out of scope for TASK-245.

Not committed -- leaving the diff for review.
<!-- SECTION:NOTES:END -->
