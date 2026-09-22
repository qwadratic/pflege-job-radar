---
id: TASK-111.5
title: 'Fix Zoho mailbox access: IMAP on boxes 2, 4, 5 and password for box 1'
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
ordinal: 116000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Preflight 2026-09-17: anastasiya.yeremenko@bewerbung-pflege.work fails SMTP and IMAP with invalid credentials; maria@bewerbung-pflege.work, evelina.vihandt@bewerbungpflege.work, dana@pflege.works have IMAP disabled. Needed to read their history and to use them later.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Preflight shows SMTP 235 and IMAP INBOX select OK for all 8 Zoho boxes
<!-- AC:END -->
