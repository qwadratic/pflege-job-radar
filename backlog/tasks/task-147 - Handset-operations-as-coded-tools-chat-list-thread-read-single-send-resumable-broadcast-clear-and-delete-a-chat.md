---
id: TASK-147
title: >-
  Handset operations as coded tools: chat list, thread read, single send,
  resumable broadcast, clear and delete a chat
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 12:51'
updated_date: '2026-09-22 06:08'
labels: []
dependencies: []
ordinal: 155000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every handset operation so far has been an ad-hoc script written from scratch by whoever needed it that hour, which is how a destructive step gets one review and no verification. Ivan asked for the operations to exist as code on the mini that a model or an operator calls in one step: send one message, run a broadcast, delete a conversation. Around those sit the reads they need to be safe (list the chats, read one thread) and the sibling of delete (clear the history, keep the chat). The broadcast is the piece that cannot live in a script: it must survive a restart of pflege-wa-bridge.service mid-run without re-sending a delivered recipient, which means its state belongs in the ledger. The destructive pair is the piece that cannot live in a script either: on-screen identity has to be proved against what the caller named before anything is tapped, and the result has to be verified afterwards. Lane: bridge/** and its deployment to the mini only.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 list_chats returns the handset's chat list: display name, resolved E.164 where the handset can name one, unread count, and whether the row is archived; it is read-only
- [x] #2 read_thread returns the visible bubbles of one chat with direction, clock and delivery tick
- [x] #3 send_message is callable as a function, not only as an HTTP handler, and keeps the ledger-first, one-flock-per-bubble, bubble-re-read, tick-or-504 guarantees unchanged
- [x] #4 send_broadcast accepts many recipients each with its own deterministic key, is paced by the existing governor with no new caps, keeps per-item state in the ledger, resumes after a restart without re-sending a delivered item, does not abort the run when one recipient fails, and can be stopped by the caller
- [x] #5 clear_chat and delete_chat require the expected chat identity and an explicit confirm flag, report what they are about to destroy before acting, refuse when the on-screen chat is not the one named, verify the result afterwards and fail loudly when it cannot be verified, and append an audit row to the ledger
- [x] #6 All of it is reachable over the existing loopback HTTP API with the bearer token and the existing error envelope shape
- [x] #7 Offline tests with FakeDriver cover: broadcast resume after a simulated crash, a refused item not stopping the run, clear/delete refusing on identity mismatch and on a missing confirm flag, a verification failure being an error, and the audit row being written
- [x] #8 Deployed to the mini and proven: the service is healthy and the new routes answer
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. errors.py: three codes for the new surface -- chat_not_found 404, chat_identity_mismatch 409, destruction_unverified 504 (the rail's 'never claim an unverified outcome' rule, applied to a destruction).
2. driver.py: ChatRow, and four verbs on PhoneDriver -- list_chats, open_chat_row, clear_chat_history, delete_chat_row. FakeDriver grows a chat book so the failure modes (clear leaves residue, delete leaves the row, title collision) are reachable with no phone.
3. adb_driver.py: the verbs against WhatsApp 2.26.36.74 ids read off the handset today -- contact_row_container rows, conversations_row_contact_name, conversations_row_message_count, conversations_archive_header; long press by motionevent DOWN/UP; menuitem_conversations_delete + alertTitle/button1 dialog; overflow item 'Chat leeren' + primary_button bottom sheet.
4. ledger.py: broadcast_run, broadcast_item and audit tables (create-if-not-exists, so the live ledger upgrades in place); resume queries; the audit row is deliberately NOT swept.
5. operations.py (TASK-147): list_chats, read_thread, send_message, clear_chat, delete_chat. Destructive discipline: one flock acquisition for preview-plus-act, identity proved before a tap, verified after, audited always.
6. broadcast.py (TASK-147): the run store and a runner thread. The governor stays the only pacemaker -- a rail_parked refusal carries next_slot_at and the item stays queued until then. Stop is a ledger flag read before each item.
7. server.py: GET /v1/chats, GET /v1/thread, POST+GET /v1/broadcasts, GET /v1/broadcasts/<id>, POST /v1/broadcasts/<id>/stop, POST /v1/chats/clear, POST /v1/chats/delete. Handlers stay thin.
8. tests/test_bridge_operations.py, FakeDriver only.
9. Offline suite green, then rsync + systemctl --user restart on the mini, then health and the new routes answered. No chat is deleted in this phase.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built in bridge/**, deployed to the mini, offline suite green (2067 passed).

Modules: bridge/operations.py (list_chats, read_thread, send_message, clear_chat, delete_chat) and
bridge/broadcast.py (Broadcast + BroadcastRunner). The driver boundary grew four verbs --
list_chats, open_chat_row, clear_chat_history, delete_chat_row -- implemented against WhatsApp
2.26.36.74 ids read off the handset on 2026-09-21 and faked in FakeDriver. The ledger grew
broadcast_run, broadcast_item and audit; the audit table is deliberately excluded from the retention
sweep. Three error codes added: chat_not_found 404, chat_identity_mismatch 409,
destruction_unverified 504.

No new caps. The governor remains the only pacemaker: a rail_parked refusal carries next_slot_at and
the item waits for it. The two new intervals named in code are the broadcast poll (5 s, how often
the runner asks, not a rate limit) and clear_chat's include_starred default (true, because the
operation verifies the chat is EMPTY afterwards and starred messages left behind would fail that
check truthfully).

FOUND ON THE HANDSET, and it contradicts the brief: there are 10 chats, not 3. Three on the main
list, SEVEN in the archive folder (dated 23.07.26 to 17.08.26, one with an unread badge). The
authorisation on record covers the three test chats; the archived seven were not named by anybody.
GET /v1/chats now walks the archive and marks them archived:true, and acting on one needs an
explicit archived:true, so no cleanup can reach them by accident.

Live proof on the mini after the restart: GET /v1/chats returned all 10 rows with identity;
GET /v1/thread read a 5-bubble thread by title with ticks; POST /v1/chats/delete refused a missing
confirm (400), an unknown field 'confirm_delete' (400) and an unknown chat (404, nothing tapped, no
audit row); POST /v1/broadcasts refused a malformed key (400) and wrote no run. No message was sent
and no chat was touched.

Review fixes (two reviewers, 12 findings) — 10 fixed, 2 rejected as already-correct/misread; all offline.

BLOCKERS
- broadcast.step(): an unclassified exception left the item 'queued' and next_due_item orders by (run.created_at, position), so the runner re-raised on the same item every poll and no other run ever started. It is now resolved against its own item as failed/executor_error (whether the handset was touched is exactly what is unknown, so it is terminal like send_unconfirmed) and the exception still leaves step(); the runner's last_error and journal row now name the run and the key.
- Constraint vocabulary is now closed and checked at the boundary (governor.CONSTRAINT_TYPES + validate_constraints, called from Governor.check and Broadcast.create). A null or a string reached int()/float() deep inside the fuse: 500 on /v1/messages, a permanently wedged run on /v1/broadcasts. An unknown key (min_gap_sec vs min_gap_ms) is a 400 naming it instead of a campaign paced by the 4 s floor.
- operations._destroy(): the audit row is written BEFORE the destructive verb (append_audit -> finish_audit), the write-ahead shape ledger.begin already uses. It was appended after the rescan, so a DriverError out of list_chats/read_bubbles/park left a destroyed conversation with no audit row and a 500. Both verifiers now catch DriverError -> destruction_unverified (504) with the reason as the proof's 'why', and park() is guarded and journalled like executor.send does it. Root cause of the raw-timeout half fixed at the source: Adb.run/connected translate subprocess.TimeoutExpired into DriverError.
- tools/wa_bridge.py: --archived is the operator's assertion again. It was derived from the row the CLI had just listed (archived chats destroyed with no one asserting it) while an explicit --archived was parsed and dropped. find_chat now filters on it and refuses a row that disagrees, both directions, and args.archived is what is posted.

MAJOR
- _verify_cleared: the verdict is 'still on the list AND reads back empty'. chat_present was computed and reported but not part of the verdict, so a clear whose tap deleted the chat passed (opening a number draws an empty thread).
- adb_driver._select_row: the long press is aimed with bounds from an earlier dump and the list reorders on any inbound message. The point pressed is now re-read out of the dump wait_for already returns and has to still carry the named title; otherwise the selection is backed out and it refuses. Nothing downstream names the chat (the delete dialog says 'Diesen Chat löschen?').
- _rows_everywhere deduped scrolled pages on (title, stamp) — 'GESTERN' is what every older row carries, so two chats sharing a display name collapsed into one and the ambiguity refusal never fired. Pages are stitched on their overlap instead; pages that do not overlap are a loud DriverError. _locate's end-of-list test is now the page, not a set.

MINOR
- Broadcast.stop() leaves a run that is not open alone (a late stop rewrote a completed campaign as stopped and overwrote finished_at).
- CLI _print_run branches on run.state: queued items on a non-open run are exit 1 and 'never attempted', not exit 3 and 'ask again' about a run whose answer can never change. --pacing must be a JSON object (usage error, not a TypeError).

REJECTED
- 'step() should also return rather than re-raise': re-raising is what keeps the bug loud in /v1/health and the journal; only the item bookkeeping was missing.
- 'docstring-only fix for the dedupe key': the docstring was wrong because the code was; changing the prose would have left GET /v1/chats under-reporting a handset.

TESTS: 24 new offline tests (operations, adb, CLI). The archived gate is now mutation-checked: removing 'and r["archived"] == archived' from _match_row fails two tests. Suite: 2151 passed, 127 skipped, 70 deselected.

Audit 2026-09-22: all 8 ACs were already checked and the offline suite was green (2151 passed at the time), but the task was left In Progress with no final summary -- finalizing it now to match the work already recorded above. Re-verified 2026-09-22: bridge/operations.py and bridge/broadcast.py exist, tests/test_bridge_operations.py passes offline, and the full suite is still green (2312 passed, grown since by TASK-150/151/152).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Handset operations (list_chats, read_thread, send_message, clear_chat, delete_chat) and a resumable ledger-backed broadcast are built in bridge/operations.py and bridge/broadcast.py, deployed to the mini and proven live (GET /v1/chats returned all 10 real chats including 7 archived ones nobody had authorised acting on; destructive routes refused a missing confirm, an unknown field and an unknown chat with no tap and no audit row). Two reviewers' 12 findings were fixed (10) or rejected with reasons (2), all offline. Verified by 24 new offline tests plus the full suite (2151 passing at ship time, 2312 as of this audit). Closing now: all 8 acceptance criteria were already checked and the work was complete, it had simply never been moved to the terminal status.
<!-- SECTION:FINAL_SUMMARY:END -->
