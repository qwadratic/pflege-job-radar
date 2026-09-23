---
id: TASK-279
title: >-
  GET /v1/chats still drives the phone inline, outside the FIFO queue -- and
  that is exactly the route a periodic unread check would poll
status: To Do
assignee: []
created_date: '2026-09-23 08:03'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 226000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/server.py:246. Severity: degraded. 

HOW IT HAPPENS: An operator (or the planned periodic unread poll) hits GET /v1/chats while the dispatcher is mid-op. Both take the bare flock; the loser waits out LOCK_TIMEOUT_SEC and gets device_unavailable. A candidate's queued reply can fail this way, and so can the chat-list read.

WHAT IT COSTS: Any periodic chats/unread poll becomes a permanent source of spurious 503s on the send path at whatever interval it runs, with no ordering guarantee and none of the pre-flight recovery or debug capture the dispatcher gives every other phone-touching verb.

PROPOSED DIRECTION (not a decision): Enqueue list_chats like every other phone-touching verb -- the dispatcher resolves bare method names against Operations already, so it is naming it in the route handler instead of calling it. Until then, do not build the periodic chat sweep on this route. Audit the remaining GET routes the same way (and decide what IdentityWatcher's own chat opens should do about ordering).

VERIFICATION NOTES: Confirmed on both halves. server.py:246-248 calls self.server.operations.list_chats(...) directly on the ThreadingHTTPServer handler thread, unlike every neighbouring phone route which goes through self._enqueue. operations.list_chats (operations.py:82-99) does take_phone -> driver.list_chats(include_archived=True) -> park, i.e. it drives the chat-list UI and taps into the archive folder. The module docstring at server.py:23-26 states the invariant it breaks ('every phone-touching one of them parses, then enqueues'), and the reconcile route's own comment (server.py:200-204) shows the identical omission was found and fixed for /v1/reconcile in TASK-230 while this one was not. A race with the dispatcher's current op resolves through the bare flock (adb_driver.py:383-395, PhoneLock with D.LOCK_TIMEOUT_SEC) and the loser gets device_unavailable via executor.take_phone (executor.py:389-393). Severity held at degraded rather than higher because device_unavailable is raised before any keystroke, so the send is left resendable rather than lost. Worth noting for the audit the finder asks for: IdentityWatcher also opens chats on its own 15 s schedule outside the queue, so /v1/chats is not the only remaining contender for the lock.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->
