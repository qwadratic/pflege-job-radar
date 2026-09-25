---
id: TASK-246
title: >-
  A replay with a changed body is recorded as sent with the NEW text, so the
  stored thread contains a message the candidate never received
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - wrong-answer-to-candidate
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 193000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/bridge.py:566. Severity: wrong-answer-to-candidate. 

HOW IT HAPPENS: Same failure chain as finding 1, but the action comes back identical while the wording does not -- near-certain, since the session is resumed and asked the same thing twice. The re-drive posts the same client_msg_id with a different body; the executor replays the original send's receipt; the VPS records the new text as delivered.

WHAT IT COSTS: The VPS's thread history holds text that was never delivered while the handset holds different text that was. The next turn's prompt (luna_brain.turn_context, built from wa_messages) is assembled from the phantom, so Luna refers back to a message the candidate never saw -- and if that phantom made a promise the conversation proceeds on it. GET /wa/threads shows the operator the phantom too.

PROPOSED DIRECTION (not a decision): Make _post_message treat a 200 carrying body_mismatch=true as its own outcome rather than a plain success: it is a delivered message whose text we do not have (the executor holds only the sha). Either fail the send loudly so the turn is recorded not-sent, or record the outbound row marked as "text unknown, an earlier body was delivered under this key". It must not become an ordinary sent row.

VERIFICATION NOTES: CONFIRMED end to end. ledger.classify (ledger.py:426-433) returns "mismatch" on a different sha; _mismatch_response (executor.py:848) sees entry.state == SENT (not in RESENDABLE) and falls through to _replay_response, which answers 200 with state="sent", the ORIGINAL body's tick/bubble_clock/body_sha256, plus `body_mismatch: True`. Client._post_message checks exactly four things -- the echoed client_msg_id, ok is True, state == "sent", tick in VERIFIED_TICKS (app/wa/bridge.py:560-576) -- and never reads `body_mismatch` or `replayed`. It returns client_msg_id, so api._send writes the NEW bubble text into wa_messages via ST.record_outbound (api.py:1029/1033). The mismatch is recorded only in the MINI's body_mismatch table; nothing on the VPS ever learns of it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented. Fix is in app/wa/api.py::_send, at the call site, not in bridge.py/executor.py/ledger.py (those already implement first-body-wins and already expose it).

New helper _refuse_body_mismatch(cl, wamid, body): after each cl.send_text/cl.send_buttons call and before ST.record_outbound, reads getattr(cl, "last_send", None). bridge.Client already sets last_send = out on every terminal 200 (app/wa/bridge.py:609) and out carries body_mismatch=True only on a Rule-3 replay (bridge/executor.py::_mismatch_response -> _replay_response, confirmed by reading the code: a genuine fresh send's _sent_response has no body_mismatch key at all, so it's falsy there). If last_send.body_mismatch is true, raises RuntimeError naming the wamid, instead of calling ST.record_outbound -- so the regenerated (never-delivered) text is never written into wa_messages. meta.Client has no last_send attribute, so getattr(...) is None and this is inert on the Meta rail.

The raise propagates out of _send into send_and_record's existing except block, which records wa_send_failures and re-raises -- same loud-failure/reclaimable-claim pattern already used for send_unconfirmed. Left _send_reopen_template untouched: its body is a fixed "[template:name]" label, not model-regenerated text, so the mismatch scenario this task describes does not apply there -- out of scope.

Verified the reachability claim before implementing: wa_messages.wamid is UNIQUE but nullable-until-set; on the exact scenario named in the task (the SAME bubble's first response is the one that got lost -- BridgeUnreachable/timeout before ST.record_outbound ran), the first INSERT for that wamid never happened, so the second one is the first, no UNIQUE violation, and the wrong text was written cleanly. Confirmed with tests/test_wa_bridge_client.py's existing test_a_regenerated_turn_re_posts_the_same_key_for_a_bubble_that_already_went_out, which already asserts the CLIENT layer returns success and sets last_send with body_mismatch=True in this exact situation -- that test needed no change.

Test added: tests/test_wa_bridge_window.py::test_a_body_mismatch_replay_is_never_recorded_as_the_new_text. A FakeBridge subclass sets last_send={"body_mismatch": True, ...} after send_text, as bridge.Client would on a Rule-3 replay. Asserts _send raises RuntimeError and that wa_messages has zero outbound rows for the thread afterwards. Confirmed this test fails without the fix (git-stashed app/wa/api.py, ran it: "DID NOT RAISE RuntimeError") and passes with it.

Ran narrow: tests/test_wa_bridge_window.py, tests/test_wa_bridge_client.py, tests/test_wa_bridge_ids.py -- 109 passed. Did not run the full suite (per standing instruction: one full run happens in the separate verification pass).

Status left at In Progress; acceptance criteria not checked -- that's the verification pass, not this one.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Verified app/wa/api.py:1059-1073 (_refuse_body_mismatch) and its two call sites at 1138/1143, both before ST.record_outbound. tests/test_wa_bridge_window.py::test_a_body_mismatch_replay_is_never_recorded_as_the_new_text passes (ran narrow: 183 tests across the five wa_bridge_* files, all green). Criteria genuinely satisfied.
<!-- SECTION:FINAL_SUMMARY:END -->
