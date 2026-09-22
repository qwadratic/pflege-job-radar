---
id: TASK-111.2
title: Microsoft 365 OAuth2 app scoped to our 3 mailboxes (read + send via Graph)
status: To Do
assignee: []
created_date: '2026-09-17 17:31'
updated_date: '2026-09-17 17:31'
labels:
  - email
dependencies: []
documentation:
  - backlog/docs/email/doc-1 - Email-channel-runbook.md
parent_task_id: TASK-111
priority: high
ordinal: 113000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
M365 IMAP basic auth is disabled (cannot read inbox or history of daria.s@pflege-connect.work, the warm box) and SMTP basic auth is switched off by default end of Dec 2026. All 3 M365 domains are in tenant ndtgroup864, so one app covers them. Skip if TASK-111.1 finds existing usable creds. Admin instruction is in runbook section 3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Env has M365_TENANT_ID, M365_CLIENT_ID, M365_CLIENT_SECRET, M365_CLIENT_SECRET_EXPIRES
- [ ] #2 Graph lists INBOX of all 3 M365 mailboxes
- [ ] #3 App cannot read any mailbox outside the 3 (Test-ServicePrincipalAuthorization InScope=False for another user)
<!-- AC:END -->
