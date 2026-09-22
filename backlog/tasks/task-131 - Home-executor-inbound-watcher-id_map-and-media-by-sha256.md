---
id: TASK-131
title: >-
  Executor inbound: Meta envelope, a collision-free fingerprint and
  content-addressed media
status: To Do
assignee: []
created_date: '2026-09-21 01:23'
updated_date: '2026-09-21 09:14'
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
- [ ] #1 The inbound envelope is verbatim Meta shape, and every tests/test_wa_*.py file passes unedited
- [ ] #2 48 h of dual-read on the shared test thread produce zero diff between their store read read-only over ssh and what accept_payload recorded on our side
- [ ] #3 Two inbound messages with identical text in the same minute from two different phones both land, proven by a test that replays exactly that case
- [ ] #4 Two identical inbound messages from the SAME phone on different days both land, and a genuine redelivery is dropped and logged rather than swallowed silently
- [ ] #5 Text, image, PDF, voice note and a quoted reply all land with the right kind and the right reply-to reference
- [ ] #6 Media bytes hash-match on a catch-up re-read, and the same media fetched twice returns identical bytes
- [ ] #7 A media file is attached only to a message from the same phone, proven by a test that interleaves two candidates documents
- [ ] #8 The watcher ingests only threads we own; a notification from the handset owner personal chat is never ingested and never answered
- [ ] #9 Killing the bridge mid-conversation, sending three messages and restarting replays all three exactly once
- [ ] #10 Typed replies arrive as plain text; no interactive.button_reply is ever synthesised on the wire
- [ ] #11 No id_map exists: context.id carries our own client_msg_id directly
<!-- AC:END -->
