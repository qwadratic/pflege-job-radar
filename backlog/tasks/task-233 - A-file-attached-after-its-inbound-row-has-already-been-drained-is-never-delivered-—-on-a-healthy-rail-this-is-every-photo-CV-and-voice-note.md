---
id: TASK-233
title: >-
  A file attached after its inbound row has already been drained is never
  delivered — on a healthy rail this is every photo, CV and voice note
status: In Progress
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-23 08:40'
labels:
  - rail-critique
  - loses-messages
dependencies: []
modified_files:
  - bridge/ledger.py
  - tests/test_bridge_executor.py
priority: high
type: bug
project: whatsapp
ordinal: 180000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/executor.py:681. Severity: loses-messages. 

HOW IT HAPPENS: Candidate sends a PDF CV. InboundWatcher (5 s) appends inbound row #N with media_kind='document' and no media_id. relay_pull (3 s) fetches #N, envelope.py falls through to the text branch, the webhook stores type='text' body='📄 Dokument', and the cursor + ack advance past #N. Only afterwards does MediaWatcher (5 s) pull the bytes and IdentityWatcher (15 s) call auto_match_media → link_media_auto, writing media_link against row #N's inbound_key. pull_inbound merges media only for rows with id > cursor, so the merged payload is never read. link_media_auto also stamps media_seen.attached_at, removing the file from media_queue() and unresolved_media, so no human sees it either. Re-delivering #N by hand would be dropped by the UNIQUE wamid.

WHAT IT COSTS: The candidate's document/photo/voice note is silently swallowed. Luna answers the literal placeholder '📄 Dokument' as if that were the message: the CV is never read, never classified onto the card, never transcribed, and the consent/match gate that depends on cv_text never opens. Inverted failure mode — the faster and healthier the relay, the more reliably it loses the file; it only works when the relay is down or lagging at the moment of attachment.

PROPOSED DIRECTION (not a decision): Stop releasing a row from the outbox whose payload declares a downloadable media_kind but carries no media_id, until either the link lands or a bounded grace window expires — the ledger already knows both facts and Executor.outbox is the single seam. Alternatively make the automatic path behave like the human one: have link_media_auto mint a fresh inbound row (as attach_media does with ATTACH_INBOUND_PREFIX) so the attachment arrives as a new event after the cursor instead of a retroactive edit to a delivered one. Either way unlinked_media_candidates should stop offering rows the relay has already acked.

VERIFICATION NOTES: CONFIRMED end to end, and the timing argument is right. bridge/ledger.py:652 selects `where i.id > ?` only, and bridge/executor.py:539-545 (`outbox`) releases every row immediately with no media gate — I grepped for one and there is none. bridge/envelope.py:98 needs BOTH a downloadable kind AND `media_id`, so an unlinked media row falls to the text branch and the webhook gets type='text', body='📄 Dokument' with media_kind not even present in the envelope. Cadences make the race the normal case, not the exception: relay 3 s (relay_pull.INTERVAL_SEC), MediaWatcher 5 s, IdentityWatcher 15 s — the cursor is past the row 5-20 s before link_media_auto runs. bridge/ledger.py:720 (`media_queue`: `where s.attached_at is null`) confirms the second half: the stamp in link_media_auto (ledger.py:880-883) also removes the file from the human queue and from Executor.unresolved_media, so there is no fallback path either. Re-pulling the row would not help even manually: wa_messages.wamid is UNIQUE and the webhook answers the repeat as 'duplicate'. The merge branch in pull_inbound is only exercised by tests that read from cursor 0 (tests/test_bridge_executor.py:1000+ never pull after an ack), which is why this survived. Partially foreshadowed by envelope.py:22-28, which admits the placeholder can arrive first — but it asserts the link 'merges into the payload' downstream, i.e. it documents the mechanism that this race defeats, not the race.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Re-verified TASK-233's own claims by reading bridge/envelope.py, bridge/ledger.py (pull_inbound, unlinked_media_candidates, link_media_auto), bridge/executor.py::auto_match_media, and bridge/relay_pull.py's cursor discipline -- all confirmed, verdict: must fix.
2. Rejected literal 'also link the original inbound_id unconditionally' from the sketch: it double-delivers the same file whenever the matcher beats the relay's ack (a normal timing, not rare), which the existing test suite exercises via outbox(after=0) expecting exactly one media event per file.
3. Fix in bridge/ledger.py::link_media_auto: gate on the ledger's own inbound.acked_at for the matched row. Not yet acked -> unchanged (link directly, same as today, zero behavior change for the common/working case). Already acked -> mint a second, fresh inbound row (attach_media's own minting pattern, new AUTO_LINK_INBOUND_PREFIX key) and link the file there so it lands past the relay's cursor and is delivered next poll; the acked original is also linked (only in this branch) purely to drop out of unlinked_media_candidates.
4. Point media_seen.attached_inbound_id at whichever row actually carries the delivered link, so pull_inbound's link_strength join keeps resolving on the row that is actually merged.
5. Add a regression test in tests/test_bridge_executor.py that pulls-then-acks a placeholder row (mirroring relay_pull.py's fetch-then-ack order) before running auto_match_media(), and asserts the file is still delivered on a later outbox() call.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verdict: real defect, confirmed independently, not a documented tradeoff -- fixed.

Fix: bridge/ledger.py::link_media_auto now checks inbound.acked_at for the matched row. If not yet acked, behavior is byte-for-byte unchanged (links directly to the caller-supplied inbound_id, exactly as before -- this is the common, already-working case: matcher beats the relay). If already acked (the bug's own scenario: relay drained+acked the placeholder before the matcher ran), it mints a fresh inbound row the same way Executor.attach_media/ledger.attach_media already do (new key via AUTO_LINK_INBOUND_PREFIX, append_inbound, then link_media against that new key) so the file lands past whatever cursor the relay is now at and is picked up as a normal new event next poll -- going through envelope.py's downloadable-kind branch since media_id is present on read. The already-acked original row is ALSO linked, but only in this acked branch, so it drops out of unlinked_media_candidates and can never be wrongly matched to a later, unrelated file of the same kind; media_seen.attached_inbound_id is pointed at whichever row actually carries the delivered link so pull_inbound's link_strength join (the weak/strong audit trail) keeps resolving correctly.

Deliberately did NOT implement the sceptic's sketch literally (link the original unconditionally, always, alongside a fresh row): that double-delivers the same file as two separate webhook messages whenever the matcher beats the relay's own ack, which is not rare -- IdentityWatcher can run before relay_pull's next 3s poll -- and is exactly the ordinary path the existing test suite already exercises (every pre-existing auto_match_media test reads outbox(after=0) and asserts exactly one event carries the media_id). The acked_at gate keeps that common path byte-for-byte unchanged and only takes the new, more expensive path in the specific already-acked scenario the task is about.

Test: added tests/test_bridge_executor.py::test_media_linked_after_its_row_was_already_acked_is_still_delivered. Seeds a document, records an inbound row, pulls it via outbox() and acks it (mirroring relay_pull.py's own fetch-then-ack order) BEFORE running auto_match_media(), then asserts the file is visible via a later outbox() call. Verified by hand that this test fails on the pre-fix code (empty media list -- the file is lost) and passes after the fix.

Ran tests/test_bridge_executor.py only, per instructions -- 126 passed (125 pre-existing + 1 new), no regressions. Did not run the full suite (that is the owner's single verification pass).

Scope note: did not touch bridge/envelope.py's docstring/media_kind-on-text-branch mismatch the sceptic also flagged as an aside -- it is not in this task's acceptance criteria, and this fix means the delivered event always goes through the downloadable-kind branch anyway, so that gap is never hit for TASK-233's own scenario.

Not committed -- left for owner review.
<!-- SECTION:NOTES:END -->
