---
id: TASK-345.9
title: >-
  Mailer access to Valentyn's Google Workspace box v.vihandt@ndt-group.agency
  (deferred)
status: To Do
assignee: []
created_date: '2026-09-28 07:01'
labels:
  - email
dependencies: []
parent_task_id: TASK-345
priority: low
ordinal: 262000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Deferred by Ivan on 2026-09-28: we do not manage the Google account for now. Do not start until he resumes it.

Why it exists: the clinics with real business history wrote with v.vihandt@ndt-group.agency, not with our cold boxes. These are Barmherzige Brüder Regensburg, Klinikum Weiden, Erler-Klinik, Klinik Hallerwiese, Ilmtalklinik Pfaffenhofen and Klinikum Nürnberg. A follow-up from that box would continue the old threads in Valentyn's voice. `tools/clinic_mailer.py` cannot use the box. It is on Google Workspace, and `.env` has no `MAILBOX_<n>_*` entry for it. Since 2025-05-01, Google Workspace refuses password-only sign-in from programs. SMTP/IMAP now needs an app password (which needs 2-Step Verification on the account) or OAuth. Until this task is done, warm clinics get mail from daria.s@pflege-connect.work, and signature B names Valentyn and his box as the contact.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 In allowlist mode, `tools/clinic_mailer.py` sends a test mail from v.vihandt@ndt-group.agency to an allowlist address
- [ ] #2 `tools/clinic_mailer.py watch` reads the inbox of v.vihandt@ndt-group.agency and logs a test reply
- [ ] #3 The credentials are only in `.env`, never in git, logs or chat
- [ ] #4 The runbook doc-1 records the setup steps and the pitfalls hit
<!-- AC:END -->
