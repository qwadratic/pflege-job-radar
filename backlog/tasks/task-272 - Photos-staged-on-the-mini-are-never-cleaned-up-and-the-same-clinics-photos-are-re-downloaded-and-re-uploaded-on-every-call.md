---
id: TASK-272
title: >-
  Photos staged on the mini are never cleaned up, and the same clinic's photos
  are re-downloaded and re-uploaded on every call
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 13:49'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 219000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna/tools_server.py:1016. Severity: degraded. 

HOW IT HAPPENS: Twenty candidates reach the photo step for the same Passau clinic over a week: twenty board downloads, twenty scp's, twenty files on the mini that nothing will ever remove, and up to 50s of staging on each of those twenty turns' critical paths.

WHAT IT COSTS: Unbounded disk growth on the machine that owns the handset, with no sweep and no counter, plus redundant board traffic and seconds of scp inside the turn budget for bytes already sitting there from the last candidate.

PROPOSED DIRECTION (not a decision): Key the staged filename on clinic_id + content hash so a second call for the same clinic reuses what is on the mini, and bring the directory into the hourly maintenance sweep with an age cutoff. Make host and path configurable and surface them in readiness() beside the other bridge checks.

VERIFICATION NOTES: CONFIRMED. _stage_on_mini (tools_server.py:1016-1035) scp's each photo to /home/cursorworker1/wa_luna_media under an mkstemp random basename; the local temp is unlinked in the finally at 1104-1109, the remote copy never is, and the random basename guarantees no dedupe — every candidate reaching the photo step for the same clinic causes a fresh board download and a fresh scp. retention.review_and_sweep only walks driver.list_screenshot_candidates / list_recording_candidates (retention.py:150-165, driver.py:239-245), and ledger.sweep touches only DB tables (ledger.py:1077-1106), so the directory is outside both. MINI_HOST/MINI_MEDIA_DIR are hardcoded at 981-982 with no env var, and config.readiness() (config.py:235-260) has no entry for them, so a host-alias or username change turns every photo send into a ToolError with nothing in the health view showing why.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verdict: FIX. Independently re-verified every claim in the VERIFICATION NOTES against the code
(tools_server.py mkstemp-derived remote basename, no cache/dedupe anywhere, finally block only
unlinking the local temp; retention.py/ledger.sweep both blind to this directory since staged
photos have no op_id and never touch the ledger; MINI_HOST/MINI_MEDIA_DIR hardcoded with no env
override and absent from config.readiness()) -- all confirmed, nothing the reviewer missed.

Implemented, following the sketch with two deliberate deviations (noted below):

1. app/wa/luna/tools_server.py: deterministic remote basename
   (hashlib.sha1(f"{clinic_id}:{url_path}") + suffix -- hashes the clinic/URL pair, not the
   downloaded bytes, so a cache hit skips the download entirely) plus a new _staged_already()
   ssh existence check before _download_to_temp/_stage_on_mini. On a miss (including an ssh
   failure) it falls through to the exact same download+stage path as before -- not a new
   silent fallback, just the pre-existing behaviour minus the new cache-hit shortcut.
   _stage_on_mini now takes the deterministic remote_name instead of deriving it from mkstemp.

2. app/wa/config.py: new LUNA_MEDIA_HOST/LUNA_MEDIA_DIR (WA_LUNA_MEDIA_HOST/WA_LUNA_MEDIA_DIR),
   defaulting to the old literals so an unconfigured deploy is unchanged. Surfaced in
   readiness() as raw values (luna_media_host/luna_media_dir), same treatment as
   bridge_phone_number_id -- neither is a secret.

3. bridge/server.py: new _sweep_luna_media(), age-only (no op_id, no ledger, nothing for
   retention.py's review-before-delete machinery to adjudicate), called from maintenance_once
   alongside review_and_sweep so it rides the existing hourly cadence and the existing
   TOCTOU/error guard. New LUNA_MEDIA_RETENTION_DAYS=14, a plain constant.

Deviations from the sketch, both because the codebase's own precedent disagreed with it:
 - No new WA_LUNA_MEDIA_RETENTION_DAYS env var. driver.SCREENSHOT_RETENTION_DAYS -- the closest
   precedent for "how long do we keep an artefact on the mini" -- is a bare hardcoded constant
   with no env override at all, unlike the operational intervals main() does thread through env
   vars. Followed that precedent instead of inventing a new knob nobody asked for. Flagged in
   the constant's own comment as a first number, not a reviewed one (same caveat
   SCREENSHOT_RETENTION_DAYS/relay_pull.py's *_STALE_SEC constants already carry).
 - Did not reuse WA_BRIDGE_SSH_HOST for the host alias. It is read from a different env file
   (relay.env) by a different process (the VPS relay) for a different leg (the inbound pull
   tunnel); tools_server.py has no access to that process's environment and each service in
   this codebase already names its own env var for the same physical machine rather than
   cross-importing. Named WA_LUNA_MEDIA_HOST instead, documented as intentionally separate.

Also updated deploy/wa-bridge/INSTALL.md's bridge.env/rail.env tables with the two new
variables, since both are otherwise-exhaustively documented there -- an env var nothing
documents is barely more discoverable than the hardcoded literal it replaced.

Tests (offline, no ssh/adb/network in any of them -- every seam that would touch the network is
monkeypatched):
 - tests/test_wa_luna_tools.py::test_show_clinic_photos_reuses_what_an_earlier_candidate_already_staged_on_the_mini
   -- new. Two different candidate phones reach the same clinic_id; asserts the second call's
   _download_to_temp/_stage_on_mini are never invoked. Verified it fails on pre-fix code
   (AttributeError: no _staged_already) via git stash and passes after.
 - tests/test_wa_luna_tools.py::test_show_clinic_photos_records_a_wa_messages_row_on_a_real_send
   -- updated its _stage_on_mini monkeypatch for the new (local, remote_name) signature and
   added a _staged_already monkeypatch (cache-miss) so the existing real-send path is unchanged.
 - tests/test_bridge_executor.py::test_maintenance_once_sweeps_luna_media_by_age_only -- new.
   An old and a fresh file under a tmp media dir; asserts only the old one is deleted and the
   count surfaces as swept["luna_media"]. Verified it fails on pre-fix code
   (AttributeError: no LUNA_MEDIA_RETENTION_DAYS) via git stash and passes after.

Ran only the two touched files: `.venv/bin/python -m pytest tests/test_wa_luna_tools.py
tests/test_bridge_executor.py -q` -> 293 passed. Did not run the full suite (batch verification
pass is separate, per instructions). Not committed -- diff is for review.
<!-- SECTION:NOTES:END -->
