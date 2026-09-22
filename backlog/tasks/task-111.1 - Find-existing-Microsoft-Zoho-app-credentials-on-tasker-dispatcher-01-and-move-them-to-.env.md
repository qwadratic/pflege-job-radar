---
id: TASK-111.1
title: >-
  Find existing Microsoft/Zoho app credentials on tasker-dispatcher-01 and move
  them to .env
status: In Progress
assignee: []
created_date: '2026-09-17 17:31'
labels:
  - email
dependencies: []
documentation:
  - backlog/docs/email/doc-1 - Email-channel-runbook.md
parent_task_id: TASK-111
priority: high
ordinal: 112000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
These mailboxes were already operated from this server by earlier tooling, so an Entra app (tenant ndtgroup864) or Zoho API client may already exist; reusing it removes the dependency on the M365 admin. First attempt (2026-09-17) was blocked by the Claude Code auto-mode classifier as credential exploration; needs an explicit permission rule or a pointer from Ivan to where the old tooling lives. Scope: find and copy to .env only, change nothing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Found the old mailbox tooling at /opt/clinic-dispatcher-worktrees/mailbox-sync-durable; creds live in /opt/clinic-dispatcher/data/private/{mailboxes.env,zoho_kindt_oauth.env}
- [x] #2 Copied 5 MICROSOFT_GRAPH_* + 9 ZOHO_* keys into pflege-board/.env (source names kept for tooling reuse, not M365_*), source files untouched, .env backed up
- [ ] #3 Found M365 creds verified by getting a Graph token, no mail sent
<!-- AC:END -->
