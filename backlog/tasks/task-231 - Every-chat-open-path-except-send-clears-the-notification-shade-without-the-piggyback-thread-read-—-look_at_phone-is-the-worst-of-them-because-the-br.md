---
id: TASK-231
title: >-
  Every chat-open path except send() clears the notification shade without the
  piggyback thread read — look_at_phone is the worst of them because the br
status: In Progress
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-23 08:25'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 178000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Found independently by 4 review lenses. Location: bridge/operations.py:134. Severity: loses-messages. 

HOW IT HAPPENS: The brain hits an uncertain moment mid-exchange and calls look_at_phone() (or calls it right after show_clinic_photos, as the prompt instructs). The op opens the candidate's chat. A message the candidate sends in the seconds before or during that open is dismissed from the notification shade by the open — and while WhatsApp is foregrounded it posts no MessagingStyle record at all — so InboundWatcher's next 5 s poll finds nothing. read_thread returns the bubbles to the model and throws them away; nothing is appended to the outbox. Executor._read_evidence_for does the same every 15 s for every candidate thread with a queued media file.

WHAT IT COSTS: A candidate's reply disappears with no trace: no outbox row, no inbound_unresolved journal line, no backlog. /v1/health stays green because last_ok_at is fresh and the outbox is legitimately empty. The candidate sees their message delivered and read, and never gets an answer. Worse than before TASK-229, because the model itself now triggers the chat-open at exactly the moments a candidate is most likely to be typing.

PROPOSED DIRECTION (not a decision): Any path that opens a chat under the lock should feed what it read through record_inbound, as send() does — but it must also establish which bubbles are today's, since read_open_thread's day-derivation leans on our own just-sent bubble being the newest thing in the thread. Ids dedupe for free (bridge/inbound.py keys on the local minute), so a message the shade also caught costs nothing. The three media-send verbs need the same treatment now that show_clinic_photos is model-reachable.

VERIFICATION NOTES: CONFIRMED. Operations.read_thread (bridge/operations.py:129-139) takes the phone, opens the chat, calls read_bubbles() and parks — no record_inbound anywhere in the method, unlike Executor.send which does exactly that at executor.py:213-216 and states the rule at :203. The wiring is real and current: server.py:249-253 enqueues 'read_thread', dispatcher._resolve finds it on Operations, app/wa/bridge.py:816 read_thread, tools_server.py:1143 look_at_phone → BR.Client().read_thread, and prompts.py:434-444 instructs the model to call it 'before a reply when something feels uncertain' and 'right after calling show_clinic_photos'. show_clinic_photos is now in MCP_TOOL_NAMES (luna_brain.py:106, commit 330a91c), so the media-send verbs' own admitted 'no inbound piggyback read' (executor.py:236, :293) is now on a model-reachable path. Executor._read_evidence_for (executor.py:739-754) has the identical shape and runs on IdentityWatcher's 15 s cadence against candidate threads. adb_driver.pull_inbound:1295 confirms the loss mechanism from the other side: WhatsApp only posts MessagingStyle notifications while backgrounded. ONE CORRECTION to the proposal: read_open_thread's own docstring (adb_driver.py:1301-1315) argues it is sound only because it runs right after our own send (our fresh bubble proves the bottom of the thread is today); a cold read must establish the day separately or it will mint ids the shade never minted. The defect stands; the fix is slightly more than 'feed the bubbles you already have'.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (not yet verified/closed -- owner runs one verification pass over the batch).

FIX. Added a cold-read day anchor separate from read_open_thread's send-anchored one:
- bridge/adb_driver.py: AdbDriver.today_divider_y(nodes) reads the day divider's OWN label
  ('HEUTE'/'TODAY') instead of day_separator_y's "lowest divider = today" (that assumption is
  sound only for read_open_thread, which knows its own just-sent bubble is at the bottom).
  AdbDriver.read_cold_thread(phone) uses it: bubbles below a HEUTE/TODAY divider are placed,
  everything else (incl. "no divider at all") is reported unresolved rather than guessed.
  KNOWN RESIDUAL, stated in the method's own docstring: a thread whose entire visible window is
  today's, with no earlier day above it to divide against, mints nothing on a cold read -- exactly
  a brand-new candidate's first message. Closing that needs an independent "is this chat's last
  activity today" signal (e.g. the chat list's own stamp column); not implemented here, flagged as
  further work rather than assumed away or silently guessed.
- bridge/driver.py: PhoneDriver.read_cold_thread declared; FakeDriver gets a separate scriptable
  cold_thread list (distinct from open_thread, since these callers have no send to anchor with).
- bridge/operations.py: Operations.read_thread now calls record_inbound via read_cold_thread after
  read_bubbles(), before park() -- only when addressed by phone (title-only reads have no E.164 to
  mint against). A DriverError on the piggyback is journalled (thread_read_failed) and does not
  fail the read.
- bridge/executor.py: Executor._read_evidence_for (IdentityWatcher's 15s-cadence path, the most
  reachable of the three per the task's own verification) now does the same piggyback after
  read_media_evidence() succeeds, before park(). send_photos/send_gallery now read_open_thread +
  record_inbound right after their own send (same anchor as executor.send -- no new anchor logic
  needed there, per the fix sketch). Docstrings' "no inbound piggyback read" claims updated to say
  what's still missing (ledger idempotency, governor pacing) now that this one is fixed.
- send_document was left untouched: the verification notes only cite send_photos/send_gallery
  (executor.py:236, :293) as model-reachable via show_clinic_photos; send_document isn't in
  MCP_TOOL_NAMES and wasn't part of the verified finding, so touching it would be scope creep.

TESTS (added, not just adjusted). Each one verified to fail before the fix (AttributeError /
wrong-assertion) and pass after, by stashing bridge/{adb_driver,driver,operations,executor}.py and
re-running -k on the new tests:
- tests/test_bridge_adb.py: 3 new tests on today_divider_y/read_cold_thread directly (HEUTE divider
  places what's below it; a GESTERN-only divider places nothing on a cold read, unlike the anchored
  read; no divider at all places nothing on a cold read, unlike the anchored read's "so all of it
  is today").
- tests/test_bridge_operations.py: read_thread piggyback, message injected via FakeDriver.read_hook
  (fires inside read_bubbles(), so it really is "arrived while the chat was open") and asserted in
  the outbox; a title-only read never calls read_cold_thread at all (scripted cold_thread=[object()]
  as a tripwire).
- tests/test_bridge_executor.py: _read_evidence_for piggyback via auto_match_media() with two
  candidates; send_photos and send_gallery piggyback via open_thread.

Ran narrowly per the task's instructions, not the full suite:
.venv/bin/python -m pytest tests/test_bridge_executor.py tests/test_bridge_operations.py tests/test_bridge_adb.py -q
-> 272 passed.

Not committed -- left for the owner's review of the diff.
<!-- SECTION:NOTES:END -->
