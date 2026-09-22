---
id: TASK-142
title: 'Own adb driver for the phone rail, with no import from the colleague package'
status: Done
assignee: []
created_date: '2026-09-21 09:54'
updated_date: '2026-09-21 10:19'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 150000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
bridge/driver.py wraps apps.wa_phone from a disposable agent worktree on the mini: a git worktree remove swaps every signature under us, and their send path reports 'unverified' as success. We own this rail end to end, so the adb technique is ours to reimplement from what we read there. The executor must keep running if ~/wa-phone-outreach disappears.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 bridge/adb_driver.py drives the handset with stdlib only and imports nothing from apps.wa_phone
- [x] #2 open_chat verifies the opened thread belongs to the requested E.164 before anything is typed
- [x] #3 typing switches to ADBKeyboard and restores the previous IME even when the send raises
- [x] #4 a send is reported only after our own outgoing bubble with that exact body is re-read off the UI with a delivery tick; unverified is an error
- [x] #5 the huawei01 flock is taken per bubble and released between bubbles so the colleague daemon can interleave
- [x] #6 offline tests cover the UI-dump parsing, the wrong-thread guard and the IME restore without a phone
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Read their device/whatsapp/inbox for technique, reimplement in bridge/adb_driver.py, point bridge/server.py main() at it, keep bridge/driver.py's PhoneDriver interface and FakeDriver.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
bridge/adb_driver.py replaces the wrapper around apps.wa_phone; bridge/driver.py keeps only the PhoneDriver interface, FakeDriver and the tick rules, so nothing in the tree imports that worktree any more. Technique (smsto: intent, uiautomator dump parsing, ADBKeyboard broadcast typing, bubble geometry, ticks off content-desc) was read from it and reimplemented; bridge/ is stdlib-only and runs on the mini under distro python3.12 with no venv.

Three deliberate departures from what was read there: (1) an unmatched bubble after send is a DriverError, never 'sent (unverified)'; (2) a chat header that is a contact NAME is checked against the handset address book -- their guard skipped names because a name carries no digits, so it could type into whichever thread was on screen; (3) screenshots only on escalation.

Verified: tests/test_bridge_adb.py, 20 offline tests against a scripted screen, plus the live unit on the mini reporting driver kind=adb, serial L2N4C19B14054874, WhatsApp 2.26.36.74, connected=true through GET /v1/health.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Own adb driver (bridge/adb_driver.py) with a thread guard that verifies a name header against the address book, IME save/restore in a finally, and a send that is only ever reported after our own bubble is re-read off the thread with a delivery tick. Verified by 20 offline tests (tests/test_bridge_adb.py) and by the running executor on the mini reporting driver kind=adb over /v1/health.
<!-- SECTION:FINAL_SUMMARY:END -->
