---
id: TASK-241
title: >-
  Inbound is harvested at exactly two points; every other verb that opens a chat
  destroys the notification it would have come from
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 188000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/executor.py:215. Severity: loses-messages. A comment in the code already states this limit -- the question is whether that trade is still acceptable now the rail carries live conversations. 

HOW IT HAPPENS: The brain calls look_at_phone on a candidate's thread. The op takes the flock and holds the chat open for tens of seconds. The candidate sends two more messages in that window; WhatsApp posts no notification for the chat on screen and the earlier ones are cleared by the open. The chat closes; the next dumpsys poll finds nothing. Same shape for a reconcile _scan, a photo send, or an identity-evidence read.

WHAT IT COSTS: Silent, permanent loss of a candidate's message, with no counter anywhere: no ledger row, no outbox event, inbound_seen does not move, and catchup.py cannot recover what was never recorded. The one signal that would reveal it -- the chat list's own unread badges, already read by Operations.list_chats -- is collected and thrown away.

PROPOSED DIRECTION (not a decision): Make the harvest a property of holding the phone rather than of send(): any verb that leaves a conversation open reads it back through record_inbound before parking. Ids are minute-keyed precisely so both doors mint the same id, so a double capture costs nothing. Then add the periodic reconciliation Ivan asked for: one low-frequency pass that lists chats and, for any row with an unread badge or a stamp newer than the newest inbound this ledger holds for that number, opens it and harvests. That pass is also the only mechanism that can recover a message lost while the colleague's lane held a chat open, which our side cannot otherwise see at all.

VERIFICATION NOTES: Confirmed by grep: record_inbound has exactly two call sites, executor.py:215 (the send piggyback) and executor.py:523 (drain_inbound, the shade poll). Operations.read_thread (operations.py:122-138) opens the chat, calls read_bubbles, parks -- no harvest; Executor._scan (executor.py:459-475) likewise; send_photos/send_gallery/send_document likewise (their docstrings even say 'no inbound piggyback read', executor.py:233-239); auto_match_media's _read_evidence_for opens candidate threads holding the lock (executor.py:665-675). look_at_phone (app/wa/luna/tools_server.py:1123) goes through Client.read_thread, i.e. Operations.read_thread -- so the brain's own eyes open a candidate's chat with no harvest. Correction to the finder: the brain does NOT call look_at_phone every turn; the tool description tells it to call it before an uncertain or high-stakes reply and after show_clinic_photos, so it is occasional, not per-turn. catchup.py confirmed to re-drive only rows already in the DB (app/wa/luna/catchup.py:1-27), so it cannot recover a message that was never recorded. Operations.list_chats does collect row.unread (operations.py:105) and nothing ever compares it with the ledger.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
VERDICT: FIX (agreeing with the sceptic; re-verified independently, holds).

TASK-241's own verification claim ('record_inbound has exactly two call sites') is stale against
this tree's uncommitted work. grep confirms record_inbound is now called from send() (executor.py),
send_photos, send_gallery, drain_inbound, Operations.read_thread (TASK-231), and
InboundWatcher._check_idle_dirty (TASK-234), plus indirectly via read_thread from
Operations.reconcile_unread (TASK-234). Every verb TASK-241 named EXCEPT one is already fixed by
this same critique batch's uncommitted work (TASK-231/234).

The one verb still genuinely unfixed: Executor._scan, the chat-open-and-read-bubbles step inside
Executor.reconcile() (POST /v1/reconcile). Confirmed by reading executor.py directly: it opens
entry.to_phone's live chat, calls read_bubbles(), parks -- no record_inbound anywhere, no comment
claiming this is deliberate.

Confirmed this is now automatically reachable, not merely human-triggered as TASK-241's own
verification notes said: bridge/watcher.py::UnresolvedSendWatcher (TASK-235, also uncommitted in
this tree, wired into bridge/server.py::main) enqueues a "reconcile" op every
WA_BRIDGE_UNRESOLVED_SEND_INTERVAL_SEC (60s default) for every ATTEMPTING/UNCONFIRMED row from
ledger.unresolved(). bridge/dispatcher.py::_resolve("reconcile") resolves to Executor.reconcile,
which calls _scan for every entry not in SENT/RESENDABLE (i.e. exactly ATTEMPTING and UNCONFIRMED,
per bridge/ledger.py's own state comments). send_unconfirmed is a documented, observed-in-production
outcome (2 of 23 live sends, executor.py's own comment on the driver_unverified path), not
hypothetical. So the chain is real and automatic: a send times out unconfirmed -> within 60s
UnresolvedSendWatcher fires -> Executor.reconcile -> _scan opens the candidate's real thread ->
any message sitting in or arriving during that window is discarded exactly as TASK-241 describes.

Also confirmed reconcile_unread (TASK-234) cannot recover this loss after the fact: it sweeps
chats keyed on ChatRow.unread, and opening a chat clears its unread badge (TASK-234's own
operations.py docstring) the same way it clears the notification shade. A message _scan discards
is gone from every door this rail has, not merely the fast ones.

FIX: added the same piggyback used by every sibling chat-open verb (send/send_photos/send_gallery/
read_thread/_read_evidence_for) into Executor._scan, inside the same ExitStack/lock acquisition it
already holds, after read_bubbles() succeeds and before park(): read_cold_thread (not
read_open_thread -- this call has no just-sent bubble of its own to anchor "today" against, same
reasoning TASK-231 used for Operations.read_thread) -> record_inbound -> ledger.note("thread_read"
on success, "thread_read_failed" on a DriverError, same failure-isolation shape as every sibling so
a piggyback failure cannot turn a resolvable reconcile into an error. Touches no ledger
state-machine transition (confirmed_sent/confirmed_absent/indeterminate logic in _scan is
unchanged) and no flock discipline (still one acquisition, nothing held across a remote call).
send_document was left untouched, matching TASK-231's own scoping: not in MCP_TOOL_NAMES, a
mechanism proof, human-only -- re-litigating that here would be scope creep.

TEST (added, verified to fail before the fix and pass after -- reverted via `git stash push --
bridge/executor.py`, reran, restored via `git stash pop`):
tests/test_bridge_executor.py::test_a_reply_that_arrives_while_reconcile_scans_the_chat_is_still_recorded
-- scripts an ATTEMPTING send, injects an inbound message via FakeDriver.read_hook (fires inside
read_bubbles(), so it is genuinely "arrived while _scan held the chat open"), runs reconcile(),
asserts the scan's own verdict (confirmed_absent) is unchanged AND the message lands in the ledger
outbox. Fails on unfixed code (outbox stays empty); passes with the fix.

Ran narrowly per instructions, not the full suite:
.venv/bin/python -m pytest tests/test_bridge_executor.py -q -> 137 passed.

Not committed -- left for the owner's review of the diff.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
bridge/executor.py:601-606 (record_inbound piggyback inside _scan), already in git history at commit 3578e728 (ancestor of HEAD). Test at tests/test_bridge_executor.py:469, run directly and confirmed passing.
<!-- SECTION:FINAL_SUMMARY:END -->
