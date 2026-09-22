---
id: TASK-111.6
title: Rework the outreach brief into a conversation and follow-up spec
status: To Do
assignee: []
created_date: '2026-09-17 17:31'
labels:
  - email
dependencies: []
documentation:
  - backlog/docs/email/doc-1 - Email-channel-runbook.md
parent_task_id: TASK-111
priority: medium
ordinal: 117000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Source brief 'Outreach 300 клиник Баварии' (PDF, 2026-09-17) designs a one-shot 300-mail campaign (one mail per address, 11-box warmup, rotation). Our use is ongoing threads with clinics and follow-ups. Keep: UWG frame, suppression, immutable send log, no tracking, plain text, send window, Bavarian holidays, watcher classes, stop rules. Open decision for Ivan: follow-ups to non-responders (each extra cold mail is a separate UWG risk) vs follow-ups only inside replied threads.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Spec doc in backlog/docs/email lists kept, cut, and changed parts of the brief
- [ ] #2 Follow-up policy for non-responders decided by Ivan and recorded
- [ ] #3 Data model covers contact, thread, message, state, next follow-up
<!-- AC:END -->
