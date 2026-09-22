---
id: TASK-111.3
title: >-
  First deliverability test from the warm mailbox: inbox placement and auth
  headers
status: To Do
assignee: []
created_date: '2026-09-17 17:31'
labels:
  - email
dependencies: []
documentation:
  - backlog/docs/email/doc-1 - Email-channel-runbook.md
parent_task_id: TASK-111
priority: high
ordinal: 114000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
SMTP login works for daria.s@pflege-connect.work, but nothing proves mail lands in Inbox or that DKIM signing is on (DNS present is not enough). Needs seed addresses from Ivan: Gmail, Outlook.com, GMX or web.de, ideally a corporate M365 or clinic gateway. Send a realistic German plain-text business mail, not a test string.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Seed list agreed with Ivan before any send
- [ ] #2 Each seed: folder recorded (Inbox/Promotions/Spam)
- [ ] #3 Headers show spf=pass, dkim=pass, dmarc=pass
- [ ] #4 mail-tester score recorded, target >= 9/10
- [ ] #5 Sending IP from Received header checked in Spamhaus, NiX Spam, UCEPROTECT L1
<!-- AC:END -->
