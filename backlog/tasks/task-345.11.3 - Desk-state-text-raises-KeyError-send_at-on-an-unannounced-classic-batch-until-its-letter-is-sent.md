---
id: TASK-345.11.3
title: >-
  Desk state text raises KeyError 'send_at' on an unannounced (classic) batch
  until its letter is sent
status: To Do
assignee: []
created_date: '2026-10-08 06:49'
labels:
  - email
dependencies: []
parent_task_id: TASK-345.11
priority: medium
type: bug
ordinal: 334000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
`plan` without `--announce-at` writes items without send_at. tools/clinic_mailer.py item_states() reads it["send_at"] for a pending item, and tools/daria_desk.py mailing_state_text() calls state_text() on every non-announced batch file in the batches dir. Effect: while such a batch is planned and not yet sent, the desk's answers to operator mails (anything that is not stop/skip/status) fail with "Не смогла ответить на это письмо: send_at". Found 2026-10-07 by an independent review before the first unannounced send (one clinic letter); worked around by keeping the batch files out of the batches dir until launch. Requested by the mailer lane (pflege-board-25) through pflege-clawl on Ivan's word of 2026-10-08. No person names in this text.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A pending item without send_at is listed as waiting for its start, with no KeyError, in item_states, state_text and the desk state text
- [ ] #2 A test in tests/test_clinic_mailer.py (or the desk test) with an unannounced batch, red before the fix
- [ ] #3 PR merged
<!-- AC:END -->
