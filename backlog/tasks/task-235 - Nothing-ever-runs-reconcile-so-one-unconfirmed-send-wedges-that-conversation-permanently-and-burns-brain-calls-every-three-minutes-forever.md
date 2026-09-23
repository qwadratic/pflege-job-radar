---
id: TASK-235
title: >-
  Nothing ever runs reconcile, so one unconfirmed send wedges that conversation
  permanently and burns brain calls every three minutes forever
status: In Progress
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-23 09:01'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 182000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/executor.py:843. Severity: loses-messages. 

HOW IT HAPPENS: A send reaches the phone and the tick never appears (no_tick / driver_unverified / send_raised), so _escalate marks the outbound row UNCONFIRMED. The turn raises, the claim ends 'skipped_error', the wa_inbound_pending row survives, and pflege-wa-catchup.timer re-drives the turn three minutes later. The brain runs again, _send mints the same deterministic client_msg_id, ledger.classify returns 'replay' with state UNCONFIRMED, and _replay_response raises 'key is unconfirmed: reconcile before any resend'. Only POST /v1/reconcile clears that state, and nothing on either machine calls it.

WHAT IT COSTS: That reply never reaches the candidate and never can. Every catch-up pass costs a full Luna turn (claude CLI, Sonnet at effort max, plus an MCP tools server spawn) and produces nothing, forever, until a human reconciles by hand — and because the wedged row is drained first and the per-phone cap is 20 Luna calls/hour against 20 catch-up passes/hour, it starves that same candidate's genuinely new messages onto the rate_limited branch. bridge/retention.py also holds every debug artefact for the op indefinitely, since the outbound row never reaches a SAFE_OUTBOUND_STATE.

PROPOSED DIRECTION (not a decision): Give reconcile a driver on the mini. The hourly maintenance loop already runs and ledger.unresolved() (ledger.py:487-492) already returns exactly the rows that need answering, oldest first; a reconcile pass over them belongs there, or on a shorter cadence since three minutes of catch-up retries is the clock that actually matters. Separately, catch-up should stop re-running the brain for a turn whose send key is in a non-resendable state — that is a known-open question, not a reply to regenerate.

VERIFICATION NOTES: CONFIRMED for the wedge; TWO impact details corrected. The chain holds: _escalate (executor.py:412-427) → mark_unconfirmed; api.py:928-930 finishes the claim 'skipped_error'; store.py documents that as reclaimable; drain_pending (api.py:663-673) keeps the pending row; the catchup timer re-drives every 3 min with no attempt cap anywhere; ledger.classify (ledger.py:434-436) returns 'replay' because UNCONFIRMED is not in RESENDABLE; _replay_response (executor.py:841-845) raises send_unconfirmed. I grepped every caller of reconcile: only tools/wa_bridge.py:685 (operator CLI) and tests. maintenance_once (server.py:395-407) does the ledger sweep and retention review only; BroadcastRunner, catchup.py and every unit in deploy/ do not touch it. CORRECTION 1: 'every subsequent message on the thread queues behind the same pending row' is wrong — drain_pending runs with raise_errors=False from both the webhook worker (api.py:580) and catch-up, and `continue`s past the failed message, so a newer inbound message gets its own turn_key and its own keys and sends normally. CORRECTION 2 (which replaces it with a real starvation effect): pending rows are drained oldest first, so the wedged row consumes a Luna call on every pass — 20 passes/hour against WA_LUNA_MAX_CALLS_PER_HOUR=20 (config.py:180), so it eats that phone's whole hourly brain budget and a genuinely new message from the same candidate repeatedly lands on the 'rate_limited' branch (api.py:880-885) and is deferred. The retention claim is right: retention.py:96-106 holds every artefact while the outbound row is not in SAFE_OUTBOUND_STATES.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
VERDICT: FIX (agreeing with the sceptic's argument; re-verified the chain independently, holds).

Implemented: bridge/watcher.py::UnresolvedSendWatcher, a new periodic driver (same shape as
BroadcastRunner/ReconcileWatcher/IdentityWatcher). Each cycle it reads ledger.unresolved()
(ATTEMPTING/UNCONFIRMED rows, oldest first) and, if non-empty, enqueues ONE "reconcile" op onto
ops_dispatcher with every unresolved client_msg_id -- the exact op kind POST /v1/reconcile already
uses (server.py:199-205), so Executor.reconcile's existing three-valued logic and
bridge/retention.py's review-before-delete are untouched. Default interval
WA_BRIDGE_UNRESOLVED_SEND_INTERVAL_SEC=60s (env-overridable, not a cap), chosen to be shorter than
pflege-wa-catchup.timer's 3-minute cadence -- the clock this exists to race, per the task's own
proposed direction.

Wired into bridge/server.py::main() next to ops_dispatcher (needs it, so built after), stopped in
the finally block with the other watchers, and surfaced at executor.health()["unresolved_send_watcher"]
the same way every other watcher's heartbeat is (bridge/executor.py: new attribute + health() key).

NAMING NOTE for whoever verifies next: bridge/watcher.py already had a class named
"ReconcileWatcher" (TASK-234, wired at server.py:497), but it answers a DIFFERENT "nothing ever
runs reconcile" -- it drives Operations.reconcile_unread() for unread inbound NOTIFICATIONS, not
Executor.reconcile()/ledger.unresolved() for stuck OUTBOUND sends. This task's fix is a distinct
class (UnresolvedSendWatcher) and a distinct env var (WA_BRIDGE_UNRESOLVED_SEND_INTERVAL_SEC,
deliberately not reusing WA_BRIDGE_RECONCILE_INTERVAL_SEC, which TASK-234's watcher already owns).

DUPLICATE FINDING ON THE BOARD: TASK-238 describes the identical root cause and chain (same
executor.py:412-427/841-845, same ledger.py Rule 4, same catchup.py cadence, same
WA_LUNA_MAX_CALLS_PER_HOUR math) under a different title. This fix closes both; TASK-238 should
probably be closed as a duplicate of this one rather than re-implemented, but I left that
task's status untouched since it's not the one assigned to me.

Scope kept to exactly this: did not touch bridge/executor.py's send/_replay_response path or
bridge/ledger.py's state machine, and did not touch catchup.py -- the task's own "catch-up should
stop re-running the brain for a non-resendable key" is flagged there as a separate open question,
out of scope here.

Test: tests/test_bridge_executor.py, new section "TASK-235: nothing ever called reconcile for a
send stuck attempting/unconfirmed" (6 tests). Primary one:
test_the_unresolved_send_watcher_queues_and_the_dispatcher_resolves_it -- scripts a send that gets
UNCONFIRMED (D.UNVERIFIED tick), then a clean-scan FakeDriver thread, runs one UnresolvedSendWatcher
cycle + one dispatcher cycle, asserts the row leaves ledger.unresolved() with verdict
confirmed_absent, and that Rule 4 then allows a resend under the same key/body. Verified this test
(and 3 siblings) fail with AttributeError when bridge/watcher.py's fix is reverted (git stash), and
pass with it restored. Ran narrow: .venv/bin/python -m pytest tests/test_bridge_executor.py -q ->
134 passed. Did not run the full suite per instructions (one verification pass covers the batch).

Left at "In Progress" per instructions -- not marking Done / checking acceptance criteria here.
<!-- SECTION:NOTES:END -->
