---
id: TASK-120
title: >-
  Implement app/wa/bridge.Client with BridgeError(MetaError) and the six
  production methods
status: Done
assignee: []
created_date: '2026-09-21 01:21'
updated_date: '2026-09-22 06:07'
labels:
  - wa-transport
dependencies:
  - TASK-116
  - TASK-119
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 128000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M6, rescoped by decision-8 (2026-09-21). The duck-typed second transport behind the seam.

BridgeError subclasses M.MetaError on purpose, so every existing except M.MetaError in the harness keeps catching without being touched, and so campaign.py error classification stays BYTE-IDENTICAL: BridgeError carries .status_code, which is exactly why a 4xx maps to failed with ownership restored and anything else to uncertain.

Six production methods as before: send_text, send_buttons, send_template, media_url, download_media, get_template. Class attributes requires_freeform_window = False and supports_buttons = False.

TWO ADDITIONS from the wrap architecture:
- begin_turn: the executor needs a turn boundary to key the ledger and to hold one bubble per flock acquisition. A multi-bubble reply is many HTTP calls with one turn key, never one call the executor splits.
- wants_idempotency_key: the capability flag that tells the caller this transport needs a deterministic client_msg_id (TASK-114). The Meta client does not have it, which is how the seam stays honest for both rails.

ONE REQUIREMENT RE-DECIDED. The original acceptance said a 200 with no provider_msg_id must raise. There is no provider message id on this rail and never will be. The requirement becomes: a 200 with no verified delivery tick must raise. A tick value of unverified is not a tick -- it is a 504. Their own code does the opposite (whatsapp.py:141-142 treats it as sent, cli.py:111 then stores a NULL fingerprint, and NULLs are unlimited in a SQLite UNIQUE column); it fired on 2 of 23 live sends. Refusing it at our boundary is one of the two reasons the wrap is worth owning.

No safety nets: the client never invents an id, never invents a tick, and never downgrades an uncertain answer into a confident one.

Injectable transport= and media_transport= mirroring meta.Client, so tests need no network stub. Media needs a separate binary transport for the same reason meta.py has one: a JSON-parsing transport corrupts bytes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 BridgeError subclasses M.MetaError and carries .status_code, and every existing except M.MetaError path catches it unchanged
- [x] #2 Against a fake transport, 400/401/409/422/429 raise with those int status codes so campaign.py classifies them as failed, and 500/503/504 so it classifies them as uncertain
- [x] #3 A 504 send_unconfirmed is never auto-resent, proven by a test that drives the campaign path and asserts no second send
- [x] #4 An unreachable bridge raises status 424 on a send but None on a media call, so import_history aborts the run rather than skipping a document
- [x] #5 A 200 response with no verified delivery tick raises rather than recording a send
- [x] #6 A 200 response whose tick is unverified raises as a 504 and is never recorded as sent
- [x] #7 A 202 response is never reported as sent by the client
- [x] #8 begin_turn returns a turn boundary the executor keys its ledger on, and one turn of several bubbles is several calls under one turn key
- [x] #9 wants_idempotency_key is True on the bridge client and absent or False on the Meta client
- [x] #10 requires_freeform_window is False and supports_buttons is False on the class
- [x] #11 Tests run fully offline through the injected transports, with no network marker
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: app/wa/bridge.py (1048 lines) implements Client exactly to this spec, shipped 2026-09-21 alongside TASK-146/147/148 but never marked here. class BridgeError(M.MetaError) at line 203 with .status_code/.payload/.client_msg_id; requires_freeform_window=False, supports_buttons=False, wants_idempotency_key=True as class attributes (line 320-322); begin_turn/begin_campaign_attempt mint via app/wa/bridge_ids; all six methods present (send_text, send_buttons, send_template, get_template, media_url, download_media) plus the operations surface TASK-147/148 added on top. tests/test_wa_bridge_client.py (42 tests, all passing) name this task explicitly in their docstrings, e.g. test_the_bridges_own_status_reaches_campaigns_classification (parametrized 400/401/409/422/429->failed, 500/503/504->uncertain, docstring 'TASK-120 AC#1/#2'), test_a_200_without_a_verified_tick_is_never_a_send (AC#5), test_the_unverified_refusal_names_the_failure_it_is_refusing (AC#6), test_an_accepted_202_is_uncertain_and_keeps_the_key_for_reconciliation (AC#7), test_an_unreachable_bridge_fails_a_send_as_4xx / test_an_unreachable_bridge_aborts_an_import_run_instead_of_skipping_a_document (AC#4), test_the_bubbles_of_one_turn_are_separate_calls_with_separate_keys (AC#8), test_the_capability_flags_say_what_this_rail_can_do (AC#9/#10). All offline via injected transport= (AC#11). Full offline suite green: 2312 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/bridge.Client implements the full spec: BridgeError(MetaError) with .status_code, the six production methods, begin_turn/begin_campaign_attempt as the turn boundary, and the three capability flags (requires_freeform_window=False, supports_buttons=False, wants_idempotency_key=True). The renegotiated invariant (a 200 with no verified tick raises, unverified is a 504 and never auto-resent) is enforced in _post_message. Verified by tests/test_wa_bridge_client.py, 42 offline tests whose docstrings cite this task's ACs directly, plus a green full offline suite (2312 passed). This shipped as part of the 2026-09-21 phone-rail work (TASK-146 onward) but the task was never closed; this closes it to match the tree.
<!-- SECTION:FINAL_SUMMARY:END -->
