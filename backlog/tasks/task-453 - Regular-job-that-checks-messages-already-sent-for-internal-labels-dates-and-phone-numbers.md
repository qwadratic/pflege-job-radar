---
id: TASK-453
title: >-
  Regular job that checks messages already sent for internal labels, dates and
  phone numbers
status: To Do
assignee: []
created_date: '2026-10-07 15:17'
labels:
  - dialog
dependencies: []
priority: low
project: whatsapp
ordinal: 333000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Deferred by Ivan (priority low). A regular job, not a per-turn exit check (TASK-314 AC 2 stays at three checks), reads the messages already sent and flags internal labels, dates and phone numbers. Evidence class: a candidate once received an internal CRM-style prefix from the old bot. Requested by the WhatsApp harness lane (wa-harness) through pflege-clawl, 2026-10-07; source: Ivan 2026-10-07, notes already on TASK-314 and TASK-316 (branch docs/backlog-next-step-and-temperature). No candidate data in this text; German candidate wording only with Ivan's verbatim approval.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A scheduled job scans sent messages for internal labels, dates and phone numbers and records every hit loudly
- [ ] #2 The job is not part of the per-turn exit check; TASK-314 AC 2 is unchanged
- [ ] #3 A fixture of a sent message with an internal prefix is flagged by the job
<!-- AC:END -->
