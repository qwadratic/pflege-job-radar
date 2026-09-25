---
id: TASK-269
title: >-
  GET /v1/chats and the broadcast runner still touch the phone outside the FIFO
  queue, re-creating the flock race TASK-227 says it removed
status: Done
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 216000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/server.py:247. Severity: degraded. 

HOW IT HAPPENS: A campaign is running. A candidate replies; Luna's bubble is enqueued and reaches the front of the queue; the dispatcher calls take_phone with a 30 s deadline while the broadcast thread is 40 s into typing a cold-outreach bubble; the poll deadline passes and the op fails device_unavailable/503 even though it had already waited its turn. Separately, a GET /v1/chats arriving after two queued ops can win the flock between them and delay the second by another 33 s.

WHAT IT COSTS: The FIFO queue's two promises -- no starvation, real ordering -- hold only among HTTP-queued ops, and the two highest-traffic non-queued callers are the ones that hold the phone longest. A live candidate's reply is refused with a spurious 503 whenever a campaign is sending, which is the exact failure TASK-227's docstring says it fixed; the reply arrives on the catch-up timer minutes later instead. Worse, both module docstrings now assert a property the code does not have, which is what a reader will trust instead of checking.

PROPOSED DIRECTION (not a decision): Route list_chats through the dispatcher like every other phone verb (TASK-230 already did exactly this for reconcile after it was left behind -- same leftover, same one-line route change), and make BroadcastRunner enqueue its item instead of calling executor.send on its own thread; its pacing and stop semantics live in the ledger and do not depend on which thread does the typing. Until the runner is queued, correct the claim in server.py's and dispatcher.py's module docstrings.

VERIFICATION NOTES: CONFIRMED, including the docstring contradiction. server.py:22-25 states 'every phone-touching one of them parses, then enqueues onto the ops dispatcher ... rather than calling an Executor method inline', and dispatcher.py:8-10 states 'this is the one thread that ever calls them ... mutual exclusion fall out for free because there is exactly one caller left'. Neither holds: server.py:247-248 calls self.server.operations.list_chats inline inside _dispatch, and list_chats (operations.py:93) calls executor.take_phone itself for a pass measured at 32.5/29.8 s (bridge.py:174-177). BroadcastRunner is a separate thread (broadcast.py:231, started at server.py:489) whose step() calls self.executor.send(request) directly at broadcast.py:177, holding the flock for a full bubble. take_phone's default is LOCK_TIMEOUT_SEC=30 (driver.py), so a queued op that has already waited its turn still gets device_unavailable/503 when the broadcast thread is mid-bubble. Ordering is likewise not FIFO across the two surfaces. Partially self-aware: broadcast.py:238-240 does acknowledge 'a handset whose flock is held by the other lane', so the runner's contention is known there even though TASK-227's own two docstrings assert the opposite. Severity is degraded rather than loses-messages because device_unavailable is defined as 'nothing was typed', the key stays clean, and catchup re-drives the turn -- the candidate waits, they do not lose the reply.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
PARTIAL FIX -- list_chats routed through the queue, BroadcastRunner deliberately left as-is (argued below).

VERIFIED against the current working tree (bridge/dispatcher.py, bridge/broadcast.py, bridge/watcher.py, bridge/executor.py carry uncommitted TASK-268 changes -- read those, not the finding's own quoted snippets, which are stale against them).

1) GET /v1/chats (server.py do_GET): confirmed genuine leftover, identical shape to the TASK-230 reconcile fix. Fixed: do_GET's /v1/chats branch now calls self._enqueue("list_chats", {...}) instead of self.server.operations.list_chats(...) inline. Zero client change needed -- app/wa/bridge.py::Client._request already unwraps any {"state":"queued","op_id":...} generically, and CHATS_BUDGET_SEC (app/wa/bridge.py:193) already budgets FLOCK_WAIT_SEC on top of the list pass, i.e. the client side was already built expecting this route to queue. Test added: tests/test_bridge_operations.py::test_chats_goes_through_the_ops_queue_instead_of_calling_list_chats_inline -- asserts the RAW response carries state=="queued"/op_id (not just the final unwrapped shape, which is identical before/after and would not catch a revert). Confirmed it fails on the pre-fix code (git stash of server.py) and passes after. Narrow run: .venv/bin/python -m pytest tests/test_bridge_operations.py -q -> 66 passed.

Also closes TASK-279, an independent duplicate of this same half (different critique pass, same file/line/finding).

2) BroadcastRunner: NOT routed through the dispatcher. Arguing this should stay as-is, not "fixed" by the sketch in this task:

- The premise that dispatcher.py's/broadcast.py's docstrings assert a false absolute is stale. TASK-268 (already uncommitted in this tree) rewrote dispatcher.py's docstring to explicitly disclaim the old "exactly one caller left" line and documents BroadcastRunner/IdentityWatcher as known, reasoned exceptions with their own yield mitigation (phone_ops_queue_counts() check before a long-patience acquire). broadcast.py's step() already has that yield (TASK-268, uncommitted) plus its own test (test_a_queued_phone_op_defers_the_step_instead_of_racing_it). server.py's own claim ("every phone-touching one of them ... enqueues") is scoped to server.py's own route handlers, which it is now true of again after (1) -- it never claimed anything about BroadcastRunner. No docstring is currently lying.

- TASK-268's dispatcher.py docstring gives a concrete, still-valid reason NOT to route BroadcastRunner through the shared queue: doing so "would force a 90-150 s bubble ... to interleave inside this one thread" -- i.e. every other queued op (including a live candidate's own reply-send, the exact scenario this task cites as highest-value) would sit behind a broadcast item's full 90-150s hold in strict FIFO, which is worse for that scenario than today's failure mode (a fast ~30s device_unavailable, resendable, redriven by catchup.py within 3 minutes). Implementing this task's sketch verbatim would reverse an already-considered, already-documented trade-off without engaging with why it was made.

- The sketch's own cost estimate ("BroadcastRunner just needs the dispatcher reference threaded through ... reusing existing plumbing") does not hold up against the actual test suite. Broadcast.step() is called synchronously and directly by ~20 tests in tests/test_bridge_operations.py (test_the_runner_sends_one_item_per_step_and_records_each and neighbours), with no dispatcher thread running -- that is deliberate (this module's own file docstring: "Offline proof ... FakeDriver only -- no adb, no phone"). Swapping the direct executor.send() call for dispatcher.enqueue()+poll-ledger.op_status() the way the sketch describes only terminates if a real OpsDispatcher thread is draining the queue concurrently; none of these tests have one, so step() would hang. Making it work would mean either (a) starting a real background dispatcher thread in the test rig, which reintroduces exactly the timing-dependent nondeterminism this offline suite is built to avoid (concretely checked: test_a_queued_phone_op_defers_the_step_instead_of_racing_it becomes racy against when the background thread happens to drain the manually-enqueued row), or (b) having step() itself drive dispatcher.cycle(), which puts a second thread able to claim+run arbitrary queued ops and reintroduces a flock race between that thread and the dispatcher's own thread -- the same class of bug this task exists to close, just reshaped. Neither is "one line reusing existing plumbing"; both are a materially larger, riskier change than named.

Net: the remaining gap (a reply enqueued after a broadcast item has already started typing loses the flock race) is real but is the accepted cost of a already-argued trade-off, already degrades gracefully (catchup.py redrive within 3 minutes, not a lost message), and closing it properly is a distinct, larger piece of work than this task's estimate -- it deserves its own correctly-scoped task if it's still wanted, not a squeeze into this one.

Left at In Progress per instructions; acceptance criteria left unchecked for the verification pass.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC1/AC2 satisfied via a hybrid: /v1/chats mechanically fixed and tested (bridge/server.py:314-322, test at tests/test_bridge_operations.py:593, passes); BroadcastRunner left unqueued with a durable written argument in bridge/dispatcher.py:11-18 resting on TASK-268's already-verified yield mitigation. Flagging as a partial-but-defensible closure rather than a full mechanical fix of both halves.
<!-- SECTION:FINAL_SUMMARY:END -->
