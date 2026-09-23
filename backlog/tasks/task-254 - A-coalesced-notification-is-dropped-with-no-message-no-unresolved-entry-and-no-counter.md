---
id: TASK-254
title: >-
  A coalesced notification is dropped with no message, no unresolved entry and
  no counter
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 11:32'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 201000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/inbound.py:235. Severity: operator-blind. 

HOW IT HAPPENS: On a build where a thread's first message arrives only as a plain summary record, or when Android coalesces several messages into one, summary_text reads '3 neue Nachrichten'. _SUMMARY matches, the fallback branch is skipped, `lines` stays empty, and the unresolved loop never runs. The record evaporates: no message, no journal line, no counter.

WHAT IT COSTS: The one signal designed to catch 'the shade said something we could not turn into a message' — inbound_unresolved — stays at zero, so an operator reading /v1/health sees a quiet rail. On a build where the summary is the only record for that thread, the candidate's messages are gone with it.

PROPOSED DIRECTION (not a decision): Treat a suppressed summary as an explicit unresolved outcome with its own reason so it lands in the journal and the health counter rather than evaporating — it is the strongest available trigger for the unread-reconciliation pass proposed above. Pin the singular/plural regex against strings actually read off the handset rather than a hand-written list.

VERIFICATION NOTES: CONFIRMED as written, with the severity tempered. Traced line by line: `lines` at inbound.py:233 filters out _SUMMARY-matching message lines; the fallback at :235 is skipped when summary_text itself matches _SUMMARY; :240 sets phone='' because `lines` is falsy; the `for text, stamp in lines` loop at :243 — the ONLY place anything is appended to `unresolved` — never executes. So the record produces neither a message nor a journal line nor an increment of the inbound_unresolved counter /v1/health exposes. That blind spot is unconditional and real. Actual message LOSS is conditional: it needs a thread whose messages exist only as a summary record (the very case the :219-220 docstring says the android.text fallback was added for — 'the first message of a brand-new thread arrives on some builds'); where a MessagingStyle record exists, its lines are used and nothing is lost. Severity set to operator-blind on that basis, not loses-messages. The plural/singular observation is also correct: _SUMMARY (:68) requires 'Nachrichten'/'new messages'/'ungelesene', so a '1 neue Nachricht' string would be stored as if the candidate had typed it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fixed, narrower than the task's headline framing but for a real reason: verified two distinct failure
modes in notification_messages() off the actual parser (bridge/inbound.py), not just by reading it.

Mode 1 (the task's own framing, plural coalesced count e.g. "2 neue Nachrichten" as the only record
for a thread): messages=[] and unresolved=[] -- confirmed. Severity is tempered by a mitigation the
task's line-by-line trace didn't follow: ReconcileWatcher/reconcile_unread (TASK-234, already wired
into bridge/server.py's startup) reads the chat list's own unread badge every ~180s and, for any
unread chat, feeds real bubbles through thread_messages() -- a completely separate parser that never
touches _SUMMARY. So a plural-only drop on this door is, in the common case, recovered with its real
text within minutes. inbound_unresolved being a monotonic lifetime counter (not a live gauge) also
means "operator reads /v1/health and sees a quiet rail" needs checking inside that narrow window, not
a realistic monitoring cadence here.

Mode 2 (not in the task's headline, but named in its own VERIFICATION NOTES and confirmed by running
the parser): singular coalesced count "1 neue Nachricht" does NOT match _SUMMARY (which required the
plural "neue Nachrichten"/"new messages"/"ungelesene"), so it falls through the fallback at line 235
and is stored as if the candidate had typed "1 neue Nachricht". Reproduced directly:
messages=[('+491700000001', '1 neue Nachricht')], unresolved=[]. This is not mitigated by
ReconcileWatcher (that door only helps chats nothing has captured yet -- here something already
captured fabricated text and it's already in the outbox for Luna to answer). It also is not an edge
case: the file's own docstring at 218-220 says the android.text fallback exists specifically for a
brand-new thread's first message, which is by construction a count of one. This mode is the real,
reachable, unmitigated bug, and is why I fixed rather than skipped.

CHANGES (bridge/inbound.py only):
1. _SUMMARY regex widened to match the singular form. Note the originally proposed regex
   (`neue Nachrichten?`) does not actually work -- German pluralises Nachricht -> Nachrichten by
   adding "en", not "n", so making only the last character optional matches "Nachrichte"/"Nachrichten"
   and never "Nachricht". Used `neue Nachricht(en)?` instead (verified against both forms). English
   "new messages?" needed no such correction (plural is a plain +s).
2. In notification_messages(), the fallback at the old line 235 now also handles the case where
   summary_text matches (revised) _SUMMARY with no MessagingStyle lines: instead of silently
   producing nothing, it appends (title, "coalesced summary text, no individual message available")
   to `unresolved` so both the journal and the /v1/health counter see it.

TESTS (tests/test_bridge_relay.py):
- Extended test_a_collapsed_count_is_a_summary_and_never_a_message to also assert the new populated
  `unresolved`.
- Added test_a_collapsed_count_of_exactly_one_is_still_a_summary_and_never_a_message (the singular
  case). Confirmed both fail against the pre-fix code (by stashing only bridge/inbound.py) and pass
  with the fix: the singular case failed with unresolved == [(title, "notification record carries no
  timestamp")] pre-fix (it was going through the fabricate-a-message path, not even hitting the
  regex-miss framing cleanly since this test record also lacks a timestamp) rather than being caught
  as a coalesced summary.

Ran: .venv/bin/python -m pytest tests/test_bridge_relay.py -q -> 32 passed. Did not run the full
suite per instruction; a wider verification pass is expected to follow separately.

No other module touched. Did not commit -- owner reviews the diff.
<!-- SECTION:NOTES:END -->
