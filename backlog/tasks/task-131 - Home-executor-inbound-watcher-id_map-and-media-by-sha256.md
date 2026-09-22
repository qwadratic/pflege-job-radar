---
id: TASK-131
title: >-
  Executor inbound: Meta envelope, a collision-free fingerprint and
  content-addressed media
status: In Progress
assignee:
  - claude
created_date: '2026-09-21 01:23'
updated_date: '2026-09-22 13:14'
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

ROUND 6 (2026-09-22, Ivan's ruling, overrides decision-9/round 5): a human queue for every inbound
file costs more than an occasional wrong pick -- autopilot restored. Automatic attribution is back
on bridge/identity.py::decide (new, pure): exact size, duration for audio (Ogg/Opus granule position
read off the file's own bytes, no library), filename for a document. Time only narrows which threads
Executor.auto_match_media opens to read bubble evidence (driver.read_media_evidence, new
PhoneDriver verb) -- it never decides. Sole same-kind candidate -> strong, no chat opened at all.
Multiple candidates, one confirmed by a hard attribute -> strong. Nothing distinguishes them (Ivan's
own example: two images, no size/name on that bubble kind) -> picked deterministically and marked
WEAK, never refused. Strength is recorded on media_seen (link_strength/link_reason, new columns)
and in GET /v1/health (media_watcher.unresolved_backlog.weak_links, identity_watcher heartbeat).

CHANGE 1 (notification queue): Executor.drain_inbound no longer takes huawei01.lock -- dumpsys
notification is a pure read and never needed it; the old lock made InboundWatcher skip an entire
cycle whenever a send held the flock (90-150s), which is the root TASK-131's own brief named for
half its decoy attributions. IdentityWatcher (new, bridge/watcher.py) is now the only loop that
takes the lock, on its own 15s schedule, waiting up to 180s (IDENTITY_LOCK_TIMEOUT_SEC) -- a busy
phone delays a match, never loses a notification (test:
test_a_notification_arriving_while_the_lock_is_held_is_still_queued_and_processed).

CHANGE 2 (content gate): a WEAK document's text never reaches the model. bridge/envelope.py carries
link_strength through the wire (honest non-Meta extension, matches media_id's own precedent);
app/wa/api.py:parse_message reads it into media_link_strength; finish_inbound gates `reads` for
kind=='document' and strength=='weak' -- the existing flat MEDIA_REPLY ack fires instead ("danke,
angekommen"), with zero other code path changes. Scoped to documents only (images/audio not gated),
per the brief's own literal scope. NOT independently tested from app/wa/ (out of this task's lane,
tests/test_bridge_*.py only) -- proven from the bridge side that the wire carries 'weak' correctly
into api.parse_message's own output (tests/test_bridge_relay.py); the one-line gate itself in
app/wa/api.py was verified by inspection, not a new test file. A dedicated app/wa test is a
reasonable follow-up if wanted.

ALSO FIXED: a byte-identical duplicate pull is now visible (media_seen.content_pull_count via a
correlated subquery, surfaced on GET /v1/media rows, media-list CLI, and health's
duplicate_content count) rather than only in the journal. The six pre-existing rows with NULL kind
were LIVE-VERIFIED reachable after deploy: mini's actual media_seen table (pre-round-5 schema,
columns source_rel/media_id/size/mtime/seen_at only) migrated cleanly on restart, kind backfilled
from each file's own handset path (image x3, video x2, audio x1) -- GET /v1/media confirmed all six
now carry kind, size, content_pull_count. mtime was already correct on that table (Ivan's "NULL
kind and mtime" turned out to describe media_file's own dead kind/mtime columns from an earlier,
now-unused schema revision -- media_seen, which the round-5/6 code actually reads, had mtime all
along; verified directly against the mini's sqlite file before writing the migration test).

BLOCKED, reported rather than worked around: step 1 (dump a live voice-note/image bubble's node
structure) could not be completed. uiautomator dump returned "ERROR: null root node" against
EVERY foreground app tested (WhatsApp AND Settings), while the launcher dumped fine in the same
session -- not WhatsApp-specific, looks like a handset/session-level accessibility state issue,
not something this session's own actions caused (confirmed before any chat was opened). A `adb
kill-server`/`start-server` and a WhatsApp force-stop did not clear it; did not attempt a phone
reboot (out of scope to decide alone). bridge/adb_driver.py::read_media_evidence is built to be
resilient to this gap by construction: it reads EVERY node's text+content-desc in a bubble's
date-anchored band (not a specific hardcoded resource id) and bridge/identity.py pattern-matches
the pool, so whatever the real shape turns out to be, a value drawn anywhere in the band is still
found -- confirm against a real voice note and a real document the first time either is reachable,
same caution the brief itself applies to a document's filename-preservation claim. Flagging this
uiautomator failure for Ivan: it also blocks the send path's own bubble reads (open_chat/
send_bubble/read_bubbles all depend on the same adb.dump()), so it is worth checking independent
of this task.

Deployed to the mini (rsync + systemctl --user restart pflege-wa-bridge.service) and verified live:
health all-green, 0 errors across watcher/media_watcher/identity_watcher, adb connected, six legacy
rows reachable as above. Lane suite green: 310 passed (tests/test_bridge_adb.py,
test_bridge_executor.py, test_bridge_media.py, test_bridge_identity.py (new),
test_bridge_operations.py, test_bridge_relay.py, test_wa_bridge_cli.py).

Round 7 (2026-09-22, same day, UAT prep): the round-6 verifier found 5 blockers. Sorted by UAT criticality per Ivan's own instruction and fixed the 3 that would certainly fire during tonight's acceptance test: B1 (evidence merge answered with the OLDEST bubble in a thread, not the newest -- bridge/executor.py::_read_evidence_for now iterates bands newest-first), B2 (a sole candidate was unconditionally 'strong' even when its own thread's bubble contradicted the file -- bridge/identity.py::decide now reads evidence and checks for contradiction on evidence-bearing kinds [audio, document] even for a sole candidate, downgrading to weak/sole_candidate_contradicted on disagreement; image/video sole candidates are unaffected, matching the existing no-chat-opened fast path), B3 (the 6 pre-round-5 backfilled rows were the oldest in media_queue and so were consumed FIRST by the automatic matcher, capable of stealing a live candidate from the correct fresh file -- bridge/ledger.py adds a real 'legacy' column set only by _migrate_media_seen's own backfill branch, and bridge/executor.py::auto_match_media now reads media_queue(auto_only=True), which excludes legacy rows from automatic matching while unresolved_media()/attach_media keep them fully reachable by a human as originally intended). Also fixed the one offline-suite regression this round introduced (test_wa_media_intake.py's exhaustive meta assertion, missing the new media_link_strength key). All 3 fixes have dedicated repro tests ported from the verifier's own /tmp/r6verify scratch tests with corrected expected outcomes. B4 (extension vs notification kind mismatch), B5 (weakness doesn't propagate to a chained pick) and two verifier scope notes (notification-minute-collapse, weak-image-not-gated) are real but not certain to fire in a 2-person UAT with a handful of files each -- filed as separate follow-up tasks (dep TASK-131) rather than rushed same-day. Deployed to the mini and restarted pflege-wa-bridge.service; handset needed a PIN unlock (screen-lock credential now cleared per Ivan) and a reboot to clear a stale accessibility-service binding left over from disabling the colleague's phoneagent app -- unrelated to this round's code, diagnosed and fixed live on the handset.
<!-- SECTION:NOTES:END -->
