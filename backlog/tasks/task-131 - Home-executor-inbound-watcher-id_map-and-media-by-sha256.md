---
id: TASK-131
title: >-
  Executor inbound: Meta envelope, a collision-free fingerprint and
  content-addressed media
status: In Progress
assignee:
  - claude
created_date: '2026-09-21 01:23'
updated_date: '2026-09-22 09:54'
labels:
  - wa-transport
dependencies:
  - TASK-123
  - TASK-130
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: medium
type: feature
ordinal: 139000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M8, rescoped by decision-8 (2026-09-21). Inbound already exists; what it produces is not safe at 59 contacts.

Their inbox.py already ingests notifications and screen content and pulls media. We wrap it and build three things it does not have.

1. THE META ENVELOPE. The watcher turns what the driver saw into a VERBATIM Meta-shaped envelope and POSTs it through the tunnel. Shape fidelity is the whole trick: it is why parse_message, accept_payload, record_inbound_pending, the background worker and stt.py all stay at zero changes, and why every tests/test_wa_*.py file stays valid unedited. If they need edits, the adapter is not thin enough.

2. A COLLISION-FREE FINGERPRINT. Theirs is sha1(direction|HH:MM|text) over the DISPLAYED time, with a UNIQUE constraint and an IntegrityError that is swallowed silently. It omits the phone and the date. Two candidates answering "Ja" in the same minute collapse to one key and one of them is dropped and never answered. Ours keys on phone plus date plus occurrence index, and a dropped duplicate is logged rather than swallowed.

3. CONTENT-ADDRESSED MEDIA. wab.m.<sha256(bytes)[:20]>, so download_media is byte-identical across retries and the store can re-verify the hash on every catch-up re-read. Also fix at our boundary what their attach_media gets wrong: it selects the newest inbound row of a media kind with NO PHONE PREDICATE, so candidate A document attaches to candidate B row.

DELETED: id_map. The bridge id space IS the client id space -- there is no provider message id to map. context.id carries our own client_msg_id directly.

Also scoped out of their behaviour at our boundary: their notification poller is unscoped and ingests every non-group WhatsApp notification on the handset, including the owner personal chats, and their --auto-reply with no argument answers everyone. Our watcher ingests only threads we own.

Button replies are NOT synthesised on the wire. Typed text arrives as plain text and the button id is recovered server-side, because rewriting the payload to interactive.button_reply would record a tap that never happened.

THE ACCEPTANCE GATE CHANGES. The original 48 h mirror-mode gate is UNRUNNABLE: our wa.sqlite holds 2 threads, both flagged as test, and 0 messages, so there is nothing to mirror against. Replace it with a DUAL-READ gate: for 48 h, read their store read-only over ssh (sqlite3 -json with file:...?mode=ro) on the shared test thread and diff it against what accept_payload recorded on our side. It also keeps working after their agent regenerates the package, because it depends on their schema rather than their code.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The inbound envelope is verbatim Meta shape, and every tests/test_wa_*.py file passes unedited
- [ ] #2 48 h of dual-read on the shared test thread produce zero diff between their store read read-only over ssh and what accept_payload recorded on our side
- [x] #3 Two inbound messages with identical text in the same minute from two different phones both land, proven by a test that replays exactly that case
- [x] #4 Two identical inbound messages from the SAME phone on different days both land, and a genuine redelivery is dropped and logged rather than swallowed silently
- [ ] #5 Text, image, PDF, voice note and a quoted reply all land with the right kind and the right reply-to reference
- [ ] #6 Media bytes hash-match on a catch-up re-read, and the same media fetched twice returns identical bytes
- [ ] #7 A media file is attached only to a message from the same phone, proven by a test that interleaves two candidates documents
- [ ] #8 The watcher ingests only threads we own; a notification from the handset owner personal chat is never ingested and never answered
- [x] #9 Killing the bridge mid-conversation, sending three messages and restarting replays all three exactly once
- [x] #10 Typed replies arrive as plain text; no interactive.button_reply is ever synthesised on the wire
- [x] #11 No id_map exists: context.id carries our own client_msg_id directly
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
ROUND 4 (2026-09-22), rescoping AC#7 away from time-window/bubble-coincidence toward provable non-attachment:

1. THE CATCH-UP GATE. Persist the inbound watcher's last successful shade-read timestamp durably
   (new ledger table, written from Executor.record_inbound -- the one place a real drain_inbound
   succeeds). bridge/media.py gets a pure catchup_gate_open(file_mtime, last_shade_read_at,
   settle_sec) check, consulted before any window/evidence logic in link_files. A file whose gate is
   not open is reported OUTCOME_AWAITING_CATCHUP and retried next cycle by the existing M1 machinery
   -- never decided on an incomplete candidate set.

2. DELETE THE BUBBLE ORACLE. Remove confirmed_by_bubbles, the confirm callable end to end
   (MediaWatcher._driver_confirm, CONFIRM_LOCK_TIMEOUT_SEC, read_incoming_bubbles), and the
   OUTCOME_CONFIRM_DEFERRED/OUTCOME_NO_CORROBORATION/OUTCOME_AMBIGUOUS_CORROBORATION outcomes tied to
   it -- it cannot fire for a voice note (no text node) and is a second time-coincidence, not
   identity proof, for everything else.

3. FILENAME EVIDENCE FOR DOCUMENTS. Where a document notification carries the sender's own filename
   (not just WhatsApp's generic "Dokument"/emoji placeholder), compare it to the pulled file's own
   name: a match confirms a candidate outright, a mismatch refuses that candidate even if it was the
   sole window candidate. Kinds with no such evidence (audio, image, video, or a document notification
   that is only the generic placeholder) fall through to gate + window-uniqueness alone, with no
   chat read at all.

4. THE HUMAN ESCAPE HATCH. New Executor methods (media_unresolved / attach_media) + HTTP routes +
   Operations wiring + app/wa/bridge.Client + tools/wa_bridge.py CLI subcommand: list unresolved
   files (kind, age, size, reason -- no phone, no filename) and attach one to a phone's own pending
   media row by id, through ledger.link_media -- the same path an automatic link takes.

5. HEALTH keeps reporting the backlog grouped by outcome, with the new vocabulary.

Lane: bridge/**, tools/wa_bridge.py, app/wa/bridge.py, tests/test_bridge_*.py, tests/test_wa_bridge_cli.py.
Lane tests only while building; one full-suite run in Verify. Build on Sonnet, verify on Opus.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: partially built, correctly stays To Do -- the identity/dedup half shipped (2026-09-21, TASK-143/146) but the media half explicitly did not. Built: bridge/envelope.py mints a verbatim Meta-shaped payload from what the watcher saw (AC#1, tests/test_bridge_relay.py::test_the_envelope_is_the_shape_the_live_webhook_already_parses, ::test_the_envelope_carries_our_own_id_because_this_rail_has_no_provider_id); the collision-free fingerprint keys on phone+date+HH:MM+text+occurrence index in bridge/inbound.py:mint() (AC#3/#4, tests/test_bridge_relay.py::test_two_people_answering_ja_in_the_same_minute_get_two_ids, ::test_the_same_text_on_two_days_is_two_ids); the cursor-based relay replays exactly once across a restart (AC#9, tests/test_bridge_relay.py::test_a_cursor_survives_the_process, ::test_the_cursor_advances_only_over_items_the_server_accepted, ::test_a_redelivery_is_recorded_as_a_duplicate_and_still_advances); typed text never synthesises interactive.button_reply (AC#10, bridge/envelope.py docstring + code, message.type is always 'text'); no id_map, context.id is our own client_msg_id directly (AC#11, app/wa/bridge_ids.py). NOT BUILT: AC#5/#6/#7, content-addressed media. bridge/envelope.py's own docstring states it outright: 'a media message arrives as its notification placeholder text (camera-emoji Foto) with media_kind recorded alongside. The bytes are not on this path (TASK-131)... type stays text.' There is no wab.m.<sha256> fetch, no download_media wiring on the inbound side, and no attach_media-with-phone-predicate fix -- grepped for wab.m. and content-addressed anywhere in bridge/, only app/wa/bridge.Client.download_media exists (an OUTBOUND helper for TASK-67-style document intake, not this AC). AC#2 (the 48h dual-read gate against the colleague's live store) has no evidence of ever having been run -- it is a live, time-boxed verification step, not a code artifact, and nothing in the notes of any dependent task mentions it. AC#8 (watcher ingests only threads we own) is PARTIALLY true: groups and our own echoed bubbles are excluded (tests/test_bridge_relay.py::test_other_packages_and_groups_and_our_own_echo_are_not_inbound) but that is a package/group/echo filter, not a positive allowlist against wa_threads -- a notification from the handset owner's own 1:1 personal chat with someone who is not a candidate would still be ingested as far as this reviewer could tell. Left unchecked. Full offline suite green: 2312 passed.
<!-- SECTION:NOTES:END -->
