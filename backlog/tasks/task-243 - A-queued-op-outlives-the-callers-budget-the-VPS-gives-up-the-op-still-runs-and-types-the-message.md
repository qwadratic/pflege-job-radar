---
id: TASK-243
title: >-
  A queued op outlives the caller's budget: the VPS gives up, the op still runs
  and types the message
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - wrong-answer-to-candidate
dependencies: []
modified_files:
  - app/wa/bridge.py
  - bridge/ledger.py
  - bridge/dispatcher.py
  - bridge/server.py
  - tests/test_bridge_executor.py
  - tests/test_wa_bridge_client.py
priority: high
type: bug
project: whatsapp
ordinal: 190000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Found independently by 2 review lenses. Location: app/wa/bridge.py:483. Severity: wrong-answer-to-candidate. 

HOW IT HAPPENS: Luna queues a bubble for candidate B. The identity watcher (or the broadcast runner) is holding the flock; take_phone waits 25 s, then the send itself takes its normal 90-120 s. B's client deadline was ~90-100 s for a short body, so _await_op raises answer_timeout/504 while the op is still running. Nothing cancels it: it opens B's chat and types the reply. Add a second queuer (catchup's 3-minute timer process working a different phone, or an operator running tools/wa_bridge.py) and the op is also sitting behind a full queue position that no budget term accounts for.

WHAT IT COSTS: The candidate receives a message the system believes was never sent -- the 'delivered but recorded as failed' class this rail exists to eliminate. The stored thread that builds the next prompt is missing a message Luna actually sent, so she repeats or contradicts herself. If catch-up re-drives the turn and the brain regenerates different wording under the same deterministic reply_key, the ledger answers idempotency_conflict (executor.py:853, first-body-wins) and that bubble is never resent, leaving the DB permanently out of step with what the person read.

PROPOSED DIRECTION (not a decision): Two independent fixes. First, make the budget include everything ahead of the typing: send_timeout should carry the same FLOCK_WAIT_SEC term every other budget in that file already carries, plus a term for queue position -- GET /v1/ops/<id> can report position/queue depth cheaply, and _await_op should extend its deadline while the op is provably still ahead of the phone instead of expiring on a number derived for a call with no queue and no lock wait. Second, give up meaningfully: a client that abandons an op should be able to cancel it, and claim_next_op should refuse to start a ticket older than the budget it was accepted under -- a reply nobody is waiting for must not be typed into a live chat. Ivan picks the staleness bound. Cheap independent win: move the debug pre-shot/start_recording to after take_phone so up to 60 s of capture timeouts are not spent inside the caller's budget for an op that may then be refused anyway.

VERIFICATION NOTES: REAL, but the finder's mechanism needs correcting on one point and is stronger on another. WRONG: 'two Luna turns run concurrently on the VPS' does not happen inside one process -- api.py:574 builds ThreadPoolExecutor(max_workers=1) and api.py:554 says turns stay serialized. It IS reachable across processes: drain_pending's claim_in_flight guard (api.py:658) is PER PHONE, and app/wa/luna/catchup.py runs as its own 3-minute timer process, so the webhook worker can be mid-send for A while catchup sends for B; followups.py and tools/wa_bridge.py are two more independent queuers. STRONGER, and the finder missed it: the budget gap does not even need a second op. send_timeout (bridge.py:430-440) is max(WA_BRIDGE_TIMEOUT_SEC, 90 + len/3.2) and the 90 is derived (comment at lines 134-138) from header-wait + PAUSE_AFTER_OPEN + typing + BUBBLE_APPEAR + TICK_WAIT only. Every OTHER budget in that file adds FLOCK_WAIT_SEC=30 explicitly (DESTROY_BUDGET_SEC:180, CHATS_BUDGET_SEC:185, THREAD_BUDGET_SEC:186); send_timeout does not, while executor.send's take_phone waits the full LOCK_TIMEOUT_SEC=30 for the flock. IdentityWatcher holds that flock for a full chat open every 15 s, and BroadcastRunner holds it for a whole 90-150 s bubble (finding 5). Add TASK-228's pre-capture, which runs BEFORE the method (dispatcher.py:105-107: debug_shot = screencap with a 30 s timeout + convert with a 30 s timeout, then start_recording). CONFIRMED consequence: there is no cancel anywhere, claim_next_op (ledger.py:546) has no age check, and api.py:1029-1030 calls ST.record_outbound only after cl.send_text returns, so an abandoned op that goes on to type leaves no outbound row; send_and_record (api.py:1050) writes a send failure and re-raises. Note the post-capture does NOT extend the wait: run_one marks the op done before the post-shot and before stop_recording.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented the fix. Verified the sceptic's claims against the code and they hold; scoped the
implementation narrower than their sketch in one place (see "Deviation" below), because CLAUDE.md's
no-invented-caps rule and the task's own note ("Ivan needs to pick the staleness bound -- that's a
real open decision, not something to invent") are in real tension with "add a staleness bound to
claim_next_op". Resolved by threading the CALLER's own already-computed budget through instead of
inventing a number on the executor side.

WHAT CHANGED

1. app/wa/bridge.py: send_timeout now carries FLOCK_WAIT_SEC, same shape as DESTROY_BUDGET_SEC /
   CHATS_BUDGET_SEC / THREAD_BUDGET_SEC (one line + docstring).

2. Every app/wa/bridge.py::Client._request call now sends its own already-computed timeout as a
   header (X-Wa-Op-Budget-Sec) -- not a new number, the exact figure the call is about to poll
   GET /v1/ops/<id> against anyway. bridge/server.py's _enqueue reads it and threads it through
   OpsDispatcher.enqueue -> ledger.enqueue_op(..., budget_sec=...) -> the phone_ops row (new
   nullable column, migrated for an existing db the same way resolved_at was).

3. bridge/ledger.py::claim_next_op is now a loop, not a single select: a QUEUED row whose
   created_at + budget_sec has elapsed is expired (OP_FAILED, code "op_expired", the same
   mark_op_failed-shaped write _recover_stuck_ops already uses for a different terminal reason)
   and the scan moves on to the next queued row instead of claiming it. A row with no budget_sec
   (older/direct caller) is never expired -- unbounded stays unbounded, nothing invented.

4. bridge/ledger.py::cancel_op(op_id, now): flips a still-QUEUED row to OP_FAILED
   (code "op_cancelled"); a no-op (False) once the row is RUNNING or already terminal -- guarded
   by the same "and state = ?" claim_next_op itself relies on, so a live adb call is never
   interrupted. New route POST /v1/ops/<id>/cancel (bridge/server.py), mirroring
   /v1/ops/<id>/resolve's shape (404 for an unknown op_id, else direct answer, never queued).

5. app/wa/bridge.py::Client._await_op calls the new cancel route, fire-and-forget, right before
   raising answer_timeout -- best-effort: the op may already be RUNNING by the time it arrives, in
   which case cancel_op is a no-op and the message may still be typed (that race is inherent and
   accepted, same as the sketch's own "fire-and-forget best-effort since the op may already be
   running"). Any failure of the cancel call itself is swallowed -- it must never replace the real
   answer_timeout the caller already decided on.

DEVIATION FROM THE SKETCH: did NOT implement (2)'s "position/ahead in GET /v1/ops/<id>" +
"_await_op keeps polling past its nominal deadline while queued/ahead>0" piece. That logic needs a
notion of "one op's own worst case" while RUNNING to know when to stop extending, which is
ambiguous across kinds (send scales with body length; destroy/chats/thread have their own separate
budgets) and risks trading the reported bug (raises too early) for its mirror (never raises,
because the queue never quite stops looking like it's making progress) -- exactly the kind of
guard CLAUDE.md says not to invent without asking. Left out rather than guessed at. Also did not
move the debug pre-shot/start_recording to after take_phone (dispatcher.py on_locked hook): traced
it and confirmed several dispatched kinds (send_document/send_photos/send_gallery with a bad path)
validate and raise BEFORE ever calling take_phone, so an unconditional "capture only after the lock"
change silently drops the pre-shot for that whole refusal class and breaks
test_debug_capture_on_a_refusal_takes_an_error_shot_not_a_post_shot's existing contract -- a
real, non-trivial behaviour change, not the "cheap independent win" it reads as, and out of scope
for this pass.

TEST: tests/test_bridge_executor.py (claim_next_op staleness + expiry, cancel_op incl. the
RUNNING-is-a-no-op case, dispatcher.enqueue budget_sec wiring, the header reaching the ops row and
the cancel route over real HTTP) and tests/test_wa_bridge_client.py (send_timeout's FLOCK_WAIT_SEC
term, _request carrying the budget header, _await_op firing a best-effort cancel on giveup, and
that a failing cancel never masks the real answer_timeout). Verified each of the 6 new/adjusted
differential assertions actually fails on the pre-fix code (temporarily reverted just the
ledger.py/dispatcher.py/bridge.py hunks via Edit, ran the new tests, confirmed TypeError/
AttributeError/KeyError/HTTP 500/assertion failures as expected, then restored). Full narrow run:
`.venv/bin/python -m pytest tests/test_wa_bridge_client.py tests/test_bridge_executor.py -q` ->
211 passed. Did not run the full suite (per instructions; one verification pass covers the batch).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/bridge.py:112,440-448,465,484-528; bridge/ledger.py:~726-798; bridge/server.py cancel route + budget header wiring. Ran tests/test_wa_bridge_client.py + tests/test_bridge_executor.py together: 246 passed, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
