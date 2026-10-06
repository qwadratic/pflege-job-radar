---
id: TASK-304
title: Cold-read misses a brand-new candidate's entire first-message thread
status: To Do
assignee: []
created_date: '2026-09-25 07:59'
labels:
  - wa-transport
dependencies: []
priority: high
project: whatsapp
ordinal: 257000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-231's cold-read fix (bridge/adb_driver.py::read_cold_thread, today_divider_y) places bubbles below a HEUTE/TODAY divider on a cold read (a chat opened without our own just-sent bubble to anchor against), and correctly reports everything else unresolved rather than guessing. The gap, documented in read_cold_thread's own docstring: a thread whose ENTIRE visible window is today's, with no earlier-day divider anywhere above it, draws no divider at all -- so the cold read places nothing and mints no inbound for any of it. This is exactly the shape of a brand-new candidate's very first message (their whole chat is one bubble, today, no divider). Normally the notification shade catches it; the hole opens specifically when the chat happens to be open at that moment (idle self-check, reconcile scan, the 15s IdentityWatcher cadence, or an operator's read_thread). WHY: needs an independent 'this chat's last activity is today' signal that a cold read (no divider) can still trust -- e.g. the chat list's own timestamp/stamp column, read without opening the chat -- rather than relying on a divider that a same-day-only thread will never draw.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A cold read of a chat whose entire visible window is one day (today), with no earlier-day divider on screen, still resolves and reports its bubbles as today's rather than leaving them unresolved
- [ ] #2 The new signal (e.g. chat list's timestamp column) is read without opening the chat, so it cannot itself trigger the notification-shade loss this task exists to close
- [ ] #3 A test proves: a same-day-only cold-read thread with the new signal present resolves its bubbles; the same thread with an ambiguous/stale signal still reports unresolved rather than guessing
- [ ] #4 read_cold_thread's docstring is updated to say this residual is closed, with the new call site named
<!-- AC:END -->
