---
id: TASK-111.4
title: >-
  Landing page on all 10 mailbox domains in the aggregator's style, with
  Impressum and Datenschutz
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
ordinal: 115000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
German clinic managers open the sender domain before replying. 6 domains are GoDaddy parking pages (/lander); bewerbung-pflege.work, bewerbungpflege.work and pflege.works redirect to pflege-ndt.work (NDT landing on 185.158.133.1, no Impressum found). A parked or empty domain reads as fraud. Style must follow the main aggregator site. Decide with Ivan: one shared page or per-domain variant, and what happens to the existing NDT landing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All 10 domains serve the landing over HTTPS (no parking, no redirect loop)
- [ ] #2 Impressum (§ 5 DDG) and Datenschutzerklärung reachable from every domain
- [ ] #3 MX/SPF/DKIM/DMARC records unchanged after DNS edits (re-run preflight)
<!-- AC:END -->
