---
id: TASK-345.11.3
title: >-
  Desk state text raises KeyError 'send_at' on an unannounced (classic) batch
  until its letter is sent
status: Done
assignee:
  - '@claude'
created_date: '2026-10-08 06:49'
updated_date: '2026-10-08 06:51'
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
- [x] #1 A pending item without send_at is listed as waiting for its start, with no KeyError, in item_states, state_text and the desk state text
- [x] #2 A test in tests/test_clinic_mailer.py (or the desk test) with an unannounced batch, red before the fix
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red test on the desk's mailing_state_text with a batch that has no send_at and no round (planned without --announce-at). 2. item_states(): a pending item without send_at shows as waiting for its start. 3. Run the mailer and desk test files. 4. PR into main.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
08.10: red test test_the_mailing_state_shows_an_unannounced_one_letter_batch_that_waits_for_its_start (tests/test_daria_desk.py) failed with KeyError 'send_at' at tools/clinic_mailer.py item_states; fix is one line there (a pending item without send_at gets the detail 'ждёт запуска'). Scope kept narrow on purpose: left_to_send(), the round reports and send_scheduled() still need round and send_at, they only run for announced batches (active() lists announced ones only); an unannounced batch is sent by 'send' directly. Related design work for sending an initial letter and its follow-ups as one chain is TASK-345.11.4.

AC 'PR merged' removed: the merge of this branch is what lands the Done status on main. Validation: tests/test_daria_desk.py, test_clinic_mailer.py, test_mailer_doc.py, test_mailer_terms.py, 155 passed; pre-commit offline lane passed. Nothing is deployed: the main checkout stays on its commit until Ivan says deploy; until then the live desk keeps the old code, so an unannounced batch must stay outside the batches dir until its send (as done for the 08.10 one-letter send).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
item_states() shows a pending item without send_at as waiting for its start instead of raising KeyError, so the desk's state text and its answers to operator mails work while an unannounced batch waits. Verified by the new desk test (red with KeyError 'send_at' before the fix, green after) and the four mailer and desk test files (155 passed). Live desk runs the old code until the next deploy.
<!-- SECTION:FINAL_SUMMARY:END -->
