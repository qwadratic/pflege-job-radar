---
id: TASK-288
title: 'A failed phone send retries the whole Luna brain call, not just the send'
status: Done
assignee: []
created_date: '2026-09-23 22:07'
updated_date: '2026-09-30 18:28'
labels:
  - whatsapp
  - reliability
dependencies: []
ordinal: 241000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live incident, 2026-09-23 ~21:16-21:19 UTC: the mini bridge's phone lock was busy for 30s (reconcile_watcher: 'device_unavailable (503): phone lock busy for 30s'), causing several sends from the VPS to fail with bridge HTTP 500/504. app/wa/api.py::process_owed_turn / finish_inbound's retry path (catchup.py, every 3 min) re-runs the ENTIRE turn on each retry -- including a fresh, expensive claude -p brain call (app/wa/luna_brain.py::_live_reply) -- even though the reply had already been correctly composed and only the delivery (bridge send) failed. Effect observed live: two inbound messages from +436…8778 (Ivan's own UAT number) retried 11 and 8 times respectively over ~50 minutes, burning 19 of the 20 allowed WA_LUNA_MAX_CALLS_PER_HOUR (app/wa/config.py) calls for that phone -- a genuinely new inbound turn then got skipped_rate_cap (app/wa/api.py:884) and Ivan got no reply at all for close to an hour, self-healing only once the oldest burned calls aged out of the 1-hour rolling window (app/wa/store.py::count_recent_luna_calls). The rate cap itself worked as designed; the problem is that a pure delivery failure (compose succeeded, send did not) consumes the same budget as a full compose, so a short bridge hiccup can starve out real conversation for up to an hour afterward.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A send-only failure (brain composed successfully, bridge/executor send failed) retries the send without re-invoking the Luna brain, OR the retry accounting excludes calls whose failure was provably delivery-side, not compose-side
- [x] #2 A reproduction test exists: brain call succeeds, simulated send fails N times, asserts the brain is invoked once (or a bounded small number of times), not N times
- [x] #3 WA_LUNA_MAX_CALLS_PER_HOUR still protects against a genuine runaway/abusive conversation -- this fix narrows what counts against it, it does not raise or remove the cap
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix: app/wa/api.py::process_owed_turn now checks ST.composed_reply(c, phone, turn_key) before calling the brain -- when a prior attempt already composed a reply (persisted via ST.record_composed_reply right before send_and_record, new 'composed' column on wa_reply_turn_claims, additive migration in app/wa/store.py MIGRATIONS), the brain call, ST.record_luna_call and the LUNA_MAX_CALLS_PER_HOUR check are all skipped entirely and the retry goes straight to send_and_record with the stored bubbles/buttons/action. A brain-side failure (nothing ever composed) still recomposes fresh, unchanged from before. Both LB.turn and B.turn share the same {bubbles,buttons,slots,asked,stopped,matches,action,...} contract (luna_brain.py's own docstring), confirmed JSON-serializable (same data already round-trips through wa_threads.slots and GET /wa/threads), so one implementation covers both brains with no branching in the reuse path.

Verified: tests/test_wa_process_owed_turn.py, 3 new tests (15/15 pass) -- test_a_send_only_failure_retries_the_send_without_calling_the_brain_again (AC#1/#2: brain invoked once across 2 failed sends + 1 successful retry, same bubbles resent), test_a_brain_side_failure_still_composes_fresh_on_retry (contrast case: a compose-side failure still recomposes, proving the fix is scoped to delivery failures only), test_a_replayed_send_does_not_spend_the_rate_cap_budget_again (AC#3: count_recent_luna_calls stays at 1 after a replayed retry, so the cap still blocks a second GENUINE compose). Broader lane checked clean: tests/test_wa_process_owed_turn.py + test_wa_luna_catchup.py + test_wa_suppression.py + test_wa_bridge_window.py + test_wa_api_queue_trigger.py + test_wa_webhook_background.py + test_wa_router.py + test_wa_routing.py, 199/200 (1 pre-existing failure, missing Supabase env credential in this shell, unrelated -- confirmed by identical error before this change).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A send-only delivery failure no longer re-invokes the Luna brain on retry: the composed reply is persisted right before send_and_record and reused if the send failed, so a bridge/Meta hiccup costs one retried delivery attempt instead of a full fresh claude -p compose -- and no longer counts a second time against LUNA_MAX_CALLS_PER_HOUR. Verified with 3 new reproduction tests plus the existing suite, all green.
<!-- SECTION:FINAL_SUMMARY:END -->
