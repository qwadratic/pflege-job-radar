---
id: TASK-158
title: >-
  Identity matcher: document extension vs notification kind mismatch attributes
  to the wrong candidate
status: To Do
assignee: []
created_date: '2026-09-22 13:13'
labels:
  - wa-transport
  - media-identity
dependencies:
  - TASK-131
references:
  - bridge/media.py
  - bridge/identity.py
  - bridge/executor.py
priority: medium
ordinal: 166000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-131 round 6 (Ivan's ruling 2026-09-22) matches media by identity (size/duration/filename), not time. Blocker B4 found by the round-6 verifier: a scan sent 'as a file' lands in WhatsApp Documents/ with an image extension; bridge/media.py::kind_for_path calls it an image by extension, so it competes in the IMAGE candidate pool against whoever actually sent a photo, and can win strong/wrong. The candidate pool is keyed purely on file extension; the notification's own announced kind (which WhatsApp also shows, e.g. the notification text itself often says 'Dokument' vs 'Foto') is never reconciled against it. Deferred out of the round-6 UAT-critical fix pass (2026-09-22) -- three other blockers (B1 oldest-bubble evidence, B2 unconditional sole-candidate strong, B3 legacy rows eating live candidates) were fixed same day; this one needs a real design decision (which signal wins when they disagree?) rather than a quick patch.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A file whose extension-derived kind disagrees with its own notification's announced kind is identified as a mismatch (not silently matched into the wrong kind's candidate pool)
- [ ] #2 A repro test proves a scan-sent-as-file with an image extension no longer attaches into the image candidate pool
- [ ] #3 The chosen resolution (mismatch -> queued for a human vs. a new kind-reconciliation rule) is recorded in the task's implementation notes with the reasoning
<!-- AC:END -->
