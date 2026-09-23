---
id: TASK-225
title: >-
  Chat left open silently kills inbound capture -- no notification, no
  safety-net re-park
status: To Do
assignee: []
created_date: '2026-09-23 02:40'
updated_date: '2026-09-23 04:03'
labels: []
dependencies: []
project: whatsapp
ordinal: 172000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live 2026-09-23: send_document/send_gallery (or manual testing) left WhatsApp's Conversation activity open on +436704048778 (Ivan's test number). WhatsApp does not post a notification for a message arriving in the chat that is currently on screen (bridge/adb_driver.py pull_inbound's own docstring says exactly this, which is why park() exists). A real message ('Ich habe Interesse an einer Stelle in München', local 03:50) sat completely uncaptured for ~45+ min: dumpsys notification --noredact had ZERO NotificationRecord block for com.whatsapp (only stale Lights List stub entries), so bridge/inbound.py::notification_messages found nothing, and nothing else drains inbound while the chat sits open. tools/wa_bridge.py read (door 2, read_thread) also returned 0 messages even after the chat was freshly opened and correctly parked afterward -- worth checking as a possible separate bug in that path, or in _placed_bubbles/thread_messages, since the bubbles were visibly on screen (confirmed via a raw uiautomator dump).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Something guarantees the phone returns to parked/launcher state even when an operation crashes outside its own finally block (a periodic health-check re-park, or a watcher-side check), so a stuck-open chat cannot silently block inbound capture indefinitely
- [ ] #2 tools/wa_bridge.py read is verified against a chat with real, currently-visible unread bubbles and confirmed to find them (or the bug in that path is found and fixed)
- [x] #3 Document what a hung/quiet relay AND a hung notification door both look like from /v1/health so an operator (or the next Claude session) can tell 'genuinely quiet' from 'blind' without a manual dumpsys/uiautomator investigation
- [ ] #4 A capture-health check exists independent of any per-thread stuck_reply flag (TASK-183): stuck_reply only ages last_inbound_at/pending_inbound rows already IN our DB, so a message the capture pipeline never saw at all (this incident: last_inbound_at stayed None the whole time) is invisible to it -- indistinguishable from a candidate who never wrote. Needs something that checks the capture pipeline itself is producing events, not just that known messages get answered in time.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC1 satisfied by TASK-226: InboundWatcher's 5s idle cycle now independently detects and re-parks a stuck-open chat (idle_dirty_recovered) even with zero operations in flight -- this is the exact mechanism that would have caught this incident within one cycle. AC3 satisfied by TASK-226 + TASK-227: /v1/health now surfaces dirty_recovered, idle_dirty_recovered, and ops_dispatcher heartbeat alongside the existing watcher.cycles/errors/last_ok_at, so a quiet-and-clean rail is distinguishable from a quiet-because-blind one without manual dumpsys/uiautomator forensics. Still open: AC2 (tools/wa_bridge.py read / door-2 returning 0 messages against visibly-present bubbles) -- not diagnosed this session, needs a live check against the handset. AC4 (a capture-health check independent of any per-thread stuck_reply flag) -- TASK-226's idle_dirty_recovered check prevents THIS incident's specific cause (a stuck-open chat) proactively, but AC4 as written asks more broadly for a canary on 'the capture pipeline itself is producing events' that would also catch causes other than a stuck chat (adb disconnected, notification permission revoked, etc.) -- that broader canary was not part of the approved plan's Part A scope and has not been built. Leaving both open rather than checking them from intent alone.
<!-- SECTION:NOTES:END -->
