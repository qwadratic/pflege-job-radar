---
id: TASK-234
title: >-
  The notification shade is the only inbound door, and nothing ever reconciles
  unread chats — a human picking up the handset destroys whatever has not b
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:56'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 181000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/adb_driver.py:1298. Severity: loses-messages. A comment in the code already states this limit -- the question is whether that trade is still acceptable now the rail carries live conversations. 

HOW IT HAPPENS: The owner opens WhatsApp on the handset to read a conversation (the routine operation asked for in this very session). That cancels the chat's notification, and while the app is foregrounded WhatsApp posts no MessagingStyle record at all — so any message from the last poll interval, and any message arriving while the app is open, never reaches the shade. Same outcome when the shade is full and Android drops WhatsApp's record, when notification access is revoked by a WhatsApp update, and when the phone reboots before a poll.

WHAT IT COSTS: Lost inbound with zero signal on either machine. inbound_backlog reads 0 unacked, the watcher heartbeat is fresh, stuck_reply never fires because from the VPS's point of view nothing arrived. The only discovery paths are the candidate complaining or someone seeing an unread badge on the handset.

PROPOSED DIRECTION (not a decision): A slow reconciliation pass on the mini: list chats (including archived) every few minutes, and for every row with an unread badge or an outbound-less tail, open the thread once, feed the bubbles through record_inbound and park. Same UI-work lock discipline IdentityWatcher already has, so it can live on that thread. It is also the positive capture canary TASK-225 AC4 asks for, instead of inferring health from an empty outbox.

VERIFICATION NOTES: CONFIRMED. pull_inbound (adb_driver.py:1292-1299) is one `dumpsys notification --noredact` and nothing else; read_open_thread is called from exactly one place, Executor.send at executor.py:214. AdbDriver.list_chats does carry the unread badge (adb_driver.py:325-328 parses 'N ungelesene Nachrichten' into ChatRow.unread) and Operations.list_chats surfaces it (operations.py:105), but I traced every consumer: the /v1/chats route, the destructive preview paths, and tools/wa_bridge.py. No watcher, timer or maintenance pass touches it — server.py:466 starts maintenance_loop (ledger sweep + retention only), plus the three watchers, the broadcast runner and the dispatcher. The foregrounded-app case is the driver's own stated mechanism (adb_driver.py:1295: 'WhatsApp only posts MessagingStyle notifications while it is in the background'), so a message arriving during any 90-150 s phone op is not merely dismissed, it is never posted. ADMISSION CORRECTED to true: backlog/tasks/task-225 AC4 is explicitly open and its implementation note says the broader capture canary 'was not part of the approved plan's Part A scope and has not been built'. That admission covers the missing canary; it does not cover the missing second door, and it is no longer an acceptable trade now that the owner routinely picks up the handset to inspect threads.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Confirm the finding against current code (post-TASK-231): pull_inbound is still the only
   shade door; read_open_thread/read_cold_thread piggybacks now cover send/send_photos/
   send_gallery/read_thread/_read_evidence_for, but nothing periodic ever consumes ChatRow.unread
   -- no watcher lists chats on its own schedule. Verdict: still a real gap, fix it.
2. Add Operations.reconcile_unread(): list_chats(include_archived=True), then for every row with
   unread>0 and a resolved, unambiguous phone, call self.read_thread(phone=..., archived=...,
   include_text=False) -- reusing TASK-231's existing piggyback (read_cold_thread ->
   record_inbound) instead of re-deriving date placement. A row this can't safely address is
   skipped and journalled, never guessed.
3. Add bridge/watcher.py::ReconcileWatcher, IdentityWatcher's shape (own thread/interval, never
   raises, heartbeat), calling operations.reconcile_unread() on a slow cadence (few minutes).
   Wire into bridge/server.py's startup/shutdown/health next to the other three watchers.
4. Separately fix bridge/watcher.py::_check_idle_dirty, which currently parks a stuck-open chat
   without reading it -- discarding exactly what its own non-blocking lock probe just proved is
   free to read. Add AdbDriver.current_chat_phone() (+ PhoneDriver abstract + FakeDriver double):
   resolves whatever header is on screen via the existing resolve_counterparty, None when it
   can't be proven. _check_idle_dirty reads via read_cold_thread + record_inbound before park()
   when a phone resolves.
5. Tests: tests/test_bridge_operations.py for reconcile_unread (captures an unread chat's new
   message; skips an unresolvable/ambiguous row without raising). tests/test_bridge_executor.py
   for _check_idle_dirty reading before park (and parking cleanly with no resolvable identity).
   Run only the touched files.
6. Record notes on TASK-234, leave status In Progress, no commit -- owner verifies in one pass.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (not yet verified/closed -- owner runs one verification pass over the batch).

VERDICT: the sceptic's finding still holds against current code (post-TASK-231). pull_inbound
(dumpsys notification) is the only door that does not depend on some OTHER operation already
having a reason to open that exact chat. TASK-231 already wired the piggyback read (read_cold_thread
-> record_inbound) into send/send_photos/send_gallery/read_thread/_read_evidence_for, but nothing
periodic ever consumes ChatRow.unread -- server.py starts InboundWatcher/MediaWatcher/
IdentityWatcher/BroadcastRunner/OpsDispatcher/maintenance_loop and none of them list chats. A chat
nobody sends to, reads from or attaches media for still has zero capture paths if the shade also
misses the message (full shade, revoked notification access, a reboot before a poll) -- exactly
TASK-225's own live incident (a real message sat 45+ min uncaptured). ONE CORRECTION to the
sceptic's own verification: read_open_thread is no longer called from exactly one place --
send_photos and send_gallery already got the TASK-231 piggyback; only send_document was left out
(scoped out there as not model-reachable). Their finding #2 (_check_idle_dirty parks without
reading) is still exactly right and unfixed by TASK-231; I checked and it's a genuinely separate
gap, not subsumed by the reconciliation watcher -- opening a chat clears BOTH its notification and
its unread badge, so a chat _check_idle_dirty catches stuck-open has already had its badge cleared
by the time it fires, and reconcile_unread (keyed on unread>0) would never revisit it.

FIX, both halves of the sceptic's sketch:
- bridge/operations.py: Operations.reconcile_unread() -- list_chats(include_archived=True), then
  for every row with unread>0 and a resolved, unambiguous phone, calls self.read_thread(phone=...,
  archived=..., include_text=False). No new date-derivation logic: this reuses TASK-231's existing
  read_cold_thread piggyback inside read_thread whole, instead of re-deriving "today" for a caller
  with no just-sent anchor (that derivation already exists and is already tested). A row with no
  resolvable/unambiguous phone is skipped and journalled (reconcile_skipped); a row whose open or
  read raises (D.DriverError or E.BridgeRefusal) is journalled (reconcile_read_failed) and the
  sweep continues -- one bad row never ends the pass, same isolation _read_evidence_for already
  keeps per candidate.
  SCOPED DOWN from the sketch's "unread OR an outbound-less tail": said in reconcile_unread's own
  docstring rather than silently narrowed -- the wider signal needs a bubble read of every chat,
  not just the unread ones, a materially bigger blast radius (every chat opened every cycle instead
  of only the ones with a real badge) that this task's own verified finding did not ask for.
- bridge/watcher.py: ReconcileWatcher, IdentityWatcher's shape (own thread, own interval -- default
  180s, "a few minutes" per the finding, WA_BRIDGE_RECONCILE_INTERVAL_SEC), calling
  operations.reconcile_unread() and never raising. Takes huawei01.lock (via Operations' own
  take_phone calls) and waits for it rather than skipping, same reasoning as IdentityWatcher: a
  busy phone should delay a slow reconciliation pass, never drop a badge it already saw on the
  listing. Wired into bridge/server.py next to the other three watchers (start/stop/startup log);
  its heartbeat is in Executor.health() as "reconcile_watcher", same pattern as the others.
- bridge/watcher.py::_check_idle_dirty now reads before it parks: added
  AdbDriver.current_chat_phone() (+ PhoneDriver abstract + FakeDriver double, returning the
  already-tracked _open_phone) -- reads whatever header is currently on screen and resolves it via
  the existing resolve_counterparty, the same way a notification title is resolved. None when
  nothing sound can be said (no header, or a display name two contacts share) -- open_chat is no
  help here since it is GIVEN the phone and only verifies against it; this has nothing to verify
  against. When a phone resolves, _check_idle_dirty now calls read_cold_thread + record_inbound
  before park(), inside the same non-blocking-probed lock acquisition it already had. Unresolvable
  identity still parks (nothing else to do), it just cannot read first.
- bridge/adb_driver.py::pull_inbound's docstring now cross-references ReconcileWatcher as the door
  that does not depend on another operation already touching that chat, same as read_cold_thread's
  own docstring cross-references TASK-231.

TESTS (added, each verified to fail before the fix -- AttributeError or a wrong assertion -- and
pass after, using a `git worktree add` of unmodified HEAD plus the new test files, since this
working tree already carries other tasks' uncommitted work and a stash would have clobbered it):
- tests/test_bridge_adb.py: 4 new tests on AdbDriver.current_chat_phone() directly -- resolves an
  unsaved contact's own number header, resolves a saved contact's display name via the address
  book, None with no header on screen, None for a display name two contacts share.
- tests/test_bridge_operations.py: reconcile_unread reads every unread chat (CHATS fixture has two:
  Ivan Test unread=1, +49 170 0000002 unread=11; Soak Rail unread=0 is left alone) and the outbox
  carries what arrived; skips and journals an ambiguous row while still reconciling the other;
  never raises when one row's open fails (journalled, sweep continues, the other row still
  reconciles).
- tests/test_bridge_executor.py: a sustained-dirty conversation is now read via record_inbound
  before InboundWatcher's idle check parks it (fails before the fix: outbox stays empty); a
  sustained-dirty conversation with no resolvable identity still parks cleanly (regression guard,
  passes either way -- kept for the "unresolvable identity never blocks the park" contract).

Ran narrowly per the task's instructions, not the full suite:
.venv/bin/python -m pytest tests/test_bridge_operations.py tests/test_bridge_executor.py tests/test_bridge_adb.py -q
-> 283 passed.

Not committed -- left for the owner's review of the diff.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Confirmed live: bridge/operations.py:94, bridge/watcher.py:180,498, bridge/adb_driver.py:1384; tests/test_bridge_operations.py + test_bridge_adb.py (130 passed) and sustained_dirty tests in test_bridge_executor.py (3 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
