---
id: TASK-278
title: >-
  Pulled inbound media -- rows and bytes -- is the one durable store with no
  sweep at all
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 14:49'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 225000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/ledger.py:1077. Severity: degraded. 

HOW IT HAPPENS: Every image, video, voice note and document the handset ever receives is pulled, stored under media/store/<media_id>, and recorded in media_seen/media_file/media_link. Nothing ever deletes any of it. Meanwhile the inbound rows those media rows point at are swept after 7 days, leaving orphan links.

WHAT IT COSTS: Unbounded disk growth on the box that also runs a colleague's worker, with no ceiling and no alarm -- the most likely path to the full disk that kills the dispatcher (finding 1) and the maintenance thread (finding 4). It also contradicts the module's own stated promise that ledger rows are kept 30 days, and makes media_known_paths() (every 5 s) and media_backlog() (every /v1/health) scan a table that only grows.

PROPOSED DIRECTION (not a decision): Give a pulled file a lifetime and enforce it in the same sweep: an attached file whose inbound row has already been acked and swept has done its job (the VPS holds its own copy under C.DOCUMENTS_DIR); an unattached one older than the queue could plausibly need is a decision to make. Move the bytes and the three tables together so a media_file row never outlives its file, and take the dangling media_link/attached_inbound_id rows with them.

VERIFICATION NOTES: Verified by grep: the only 'delete from' statements anywhere in bridge/ are the seven inside sweep() (ledger.py:1082-1104), covering outbound, journal, body_mismatch, inbound, broadcast_item, broadcast_run, phone_ops. media_seen / media_file / media_link appear in none of them. The bytes match too -- watcher.py:249 moves each pull to media/store/<media_id> (dest.replace(final)) and the only unlink is the duplicate-bytes case at :252, so a stored file is never removed. inbound rows ARE swept 7 days after ack (ledger.py:1088), so media_link.inbound_id / media_seen.attached_inbound_id do dangle from day 8. This is a disk-exhaustion defect, not a retention-policy ask, so it survives the owner's compliance ban. Severity tempered: the mini had 457 G free per ledger.py's own comment, so this is a slow burn, but it is the most plausible trigger for finding 1 and there is no ceiling and no alarm.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
FIXED (not skipped). Re-verified the sceptic's claims by reading the code: confirmed sweep()
(ledger.py:1248) had 0 of its 7 deletes touching media_seen/media_file/media_link, watcher.py's
only unlink() (:337,:348) never fires for an old file, retention.py/_sweep_luna_media are unrelated
directories/tables, and no disk-usage guard exists anywhere. The dangling-link claim (media_link /
attached_inbound_id outliving the inbound row it points at, from day 8 onward) also checked out.

IMPLEMENTED the well-specified half only (attached-and-synced media), per the task's own framing of
the unattached-queue lifetime as an open decision for Ivan:

- bridge/ledger.py::sweep() now also runs, in the same locked transaction, right after the existing
  inbound delete: finds media_seen rows whose attached_inbound_id points at an inbound_key that just
  aged out (or already had, pre-fix); deletes those media_seen rows and any media_link row whose
  inbound_id no longer resolves (covers link_media_auto's acked-branch throwaway row too); then, for
  each media_id touched, deletes the media_file row ONLY IF no other media_seen row (attached-live or
  still-unattached) still names it -- media_file is content-addressed and shared across resends/
  duplicate sends, so a still-queued duplicate keeps the bytes alive. Returns the freed local_paths
  (does not touch the filesystem itself -- ledger.py had zero file I/O before this and I kept that
  split). A never-attached media_seen row (attached_inbound_id is null) is left alone, unconditionally
  -- explicitly out of scope, not swept on an invented cutoff.
- bridge/server.py::maintenance_once now unlinks those paths right after calling ledger.sweep(),
  same tolerant-of-already-gone style as AdbDriver.delete_paths (FileNotFoundError -> not an error).
  swept["media_seen"|"media_link"|"media_file"] counts ride the existing journal note alongside the
  other sweep counts.

Does not touch bridge/dispatcher.py, the outbound/phone_ops state machine, or any send path.

TEST: tests/test_bridge_retention.py, 3 new tests --
  test_maintenance_once_sweeps_an_attached_files_bytes_once_its_inbound_row_ages_out
  test_media_shared_by_a_still_unattached_duplicate_pull_keeps_its_bytes
  test_an_unattached_pulled_files_bytes_are_never_swept
Verified the first one FAILS without the fix (reverted the sweep() block, ledger returned
media_seen=0 instead of 1) and passes with it restored. Ran only tests/test_bridge_retention.py
(34 passed) per the batch's own "one full run in verify" convention -- did not run the whole suite.

LEFT OPEN, on purpose: how long a pulled-but-never-attached media_seen row should live. The task
itself names this as a decision, not a defect; inventing a cutoff here would be exactly the kind of
self-invented safety net CLAUDE.md rules out.
<!-- SECTION:NOTES:END -->
