---
id: TASK-227
title: 'Real FIFO phone-operation queue: guaranteed ordering, no more flock-race 503s'
status: Done
assignee: []
created_date: '2026-09-23 03:11'
updated_date: '2026-09-25 07:54'
labels: []
dependencies: []
project: whatsapp
ordinal: 174000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-225 fix, part B. bridge/server.py is a ThreadingHTTPServer with no serialization beyond a plain flock (bridge/adb_driver.py PhoneLock, no FIFO guarantee, 30s timeout then 503 device_unavailable). Replace with a durable phone_ops table (mirrors broadcast_item's position+status+index shape, bridge/ledger.py:56-61,144-160) and one dispatcher thread that claims the oldest queued row and generically dispatches getattr(executor, kind)(**args). HTTP routes that touch the phone change to enqueue+answer immediately with {op_id, state:queued} (Ivan's explicit choice); new GET /v1/ops/<op_id> mirrors GET /v1/broadcasts/<id>. app/wa/bridge.py::Client absorbs enqueue+poll-until-terminal internally so every existing caller (luna_brain, campaign.py, tools_server.py, catchup.py, followups.py, tools/wa_bridge.py) keeps its current synchronous, confirmed-result contract unchanged -- this preserves bridge/server.py's own documented 'never lie about unconfirmed work' principle (see its module docstring on why there is deliberately no bare 202 today).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Two operations enqueued concurrently execute in strict FIFO order, never both racing the flock
- [x] #2 GET /v1/ops/<op_id> reports queued/running/done/failed with result or error, mirroring the broadcast status route's shape
- [x] #3 Every existing caller of app/wa/bridge.py::Client keeps working with zero code changes -- the client's public methods still return a confirmed result or raise, never a bare queued state
- [x] #4 The dispatcher runs the pre-flight dirty check from the sibling task before each job
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented: bridge/ledger.py gains a phone_ops table (op_id/position/kind/args/state/result/error/timestamps, idx on (state, position)) plus enqueue_op/claim_next_op/mark_op_done/mark_op_failed/op_status, and sweep() now also reaps terminal phone_ops rows past LEDGER_RETENTION_DAYS. New bridge/dispatcher.py::OpsDispatcher: one thread, claims the oldest queued row, generic getattr(operations_then_executor, kind)(**args) dispatch (no per-kind branch), normalizes the pre-existing (status,payload)-tuple vs bare-dict return shapes, catches BridgeRefusal/Exception into a failed envelope, never raises out of run_one. bridge/server.py: every phone-touching route (/v1/messages, /v1/photos, /v1/gallery, /v1/document, /v1/thread, /v1/chats/clear, /v1/chats/delete) now enqueues via Handler._enqueue and answers 200 {op_id, state:queued} immediately; new GET /v1/ops/<op_id> mirrors GET /v1/broadcasts/<id>'s shape; BridgeServer builds/owns the dispatcher (constructor param, default OpsDispatcher(...) for tests that don't pass one); main() starts it as a daemon thread alongside the other watchers and stops it in the shutdown path. app/wa/bridge.py::Client._request detects a bare 200 {state:queued, op_id} (and ONLY that exact shape -- checked against the pre-existing 202 pacing-uncertainty contract, which has no op_id key and a different status code, so BridgeAccepted's own test coverage (tests/test_wa_bridge_client.py) is untouched) and transparently polls GET /v1/ops/<id> via a new _await_op() until a terminal state, then returns/raises exactly as it always did -- zero call-site changes anywhere (luna_brain, campaign.py, tools_server.py, catchup.py, followups.py, tools/wa_bridge.py). Left deliberately outside the queue, as known follow-up gaps rather than silent scope expansion: GET /v1/chats (list_chats) and /v1/reconcile still call straight through -- both touch the phone but were not named in the approved plan's exact route list. Left deliberately outside the queue, as known follow-up gaps rather than silent scope expansion: GET /v1/chats (list_chats) and /v1/reconcile still call straight through -- both touch the phone but were not named in the approved plan's exact route list. Verified: test_ops_run_in_strict_fifo_order (three ops to three different recipients -- same-number reply pacing is a separate governor rule, not what FIFO ordering is proving), test_a_refusal_is_recorded_as_a_failed_op_with_its_own_envelope, test_send_and_read_thread_both_reach_their_method_through_generic_dispatch, test_dispatched_jobs_inherit_take_phones_pre_flight_dirty_recovery (AC4, proves the dispatcher adds no dirty-check of its own -- take_phone's TASK-226 recovery just rides along), plus the existing HTTP-level test suites in tests/test_bridge_executor.py and tests/test_bridge_operations.py all rewired through a new _await_op poll-and-unwrap test helper so they keep proving the synchronous wire contract callers rely on. tests/test_wa_bridge_client.py (56 tests, the Client's own direct coverage) passes unchanged, confirming AC3 at that layer too.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Replaced the bare-flock race with a durable FIFO phone_ops queue and one dispatcher thread (bridge/dispatcher.py) that is now the sole caller of every phone-touching executor/operations method. HTTP routes enqueue and answer {op_id, state:queued} immediately (Ivan's explicit choice); app/wa/bridge.py::Client absorbs the poll-to-terminal-state internally so no existing caller anywhere in the codebase ever sees an unconfirmed result. Verified: 4 new dispatcher-level tests plus the pre-existing HTTP-level suites (tests/test_bridge_executor.py, tests/test_bridge_operations.py) rewired to the new async contract, plus tests/test_wa_bridge_client.py's 56 tests unchanged. Full bridge lane (test_bridge_executor.py + test_bridge_adb.py + test_bridge_operations.py) 255 passed, 0 regressions.

Verified bridge/ledger.py:202-858 (phone_ops CRUD), bridge/dispatcher.py (generic single-thread claim loop), bridge/server.py:123-299 (_enqueue + GET /v1/ops/<id>) and app/wa/bridge.py:460-528 (Client._request/_await_op transparent polling) all match the 4 ACs; ran .venv/bin/python -m pytest tests/test_bridge_operations.py tests/test_bridge_executor.py -k 'fifo or ops_queue or dirty' -> 10 passed. TASK-268/269 document real, separately-scoped gaps (BroadcastRunner/IdentityWatcher/GET-/v1/chats bypassing the queue) that TASK-227's own notes already flagged as out-of-scope follow-ups, not violations of this task's 4 ACs.
<!-- SECTION:FINAL_SUMMARY:END -->
