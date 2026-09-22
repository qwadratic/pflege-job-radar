---
id: TASK-160
title: >-
  Notification queue: two identical media notifications from one person in the
  same minute can collapse into one event
status: To Do
assignee: []
created_date: '2026-09-22 13:13'
labels:
  - wa-transport
  - media-identity
dependencies:
  - TASK-131
references:
  - bridge/watcher.py
  - bridge/inbound.py
priority: medium
ordinal: 168000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-131 round 6 verifier finding (not a round-6 regression -- pre-existing, still open, 2026-09-22): the inbound id is keyed on the local minute plus a per-snapshot occurrence index. Two photos from one person in the same minute are two distinct events only while both lines are visible together in one dumpsys notification snapshot; if the first line has already left the shade by the next poll, the second mints the same id as the first and is silently dropped as a redelivery. Media placeholder text is identical by construction ('Foto', 'Video', etc.), so this hits media notifications hardest of all message kinds. repro: test_two_identical_media_notifications_in_one_minute_across_two_polls_collapse (round-6 verifier's own scratch tests, not yet ported into the repo).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Two same-person, same-minute, same-placeholder-text media notifications are never collapsed into one event even when they land on different poll snapshots
- [ ] #2 A repro test (ported/adapted from the round-6 verifier's scratch test) is committed and passing
<!-- AC:END -->
