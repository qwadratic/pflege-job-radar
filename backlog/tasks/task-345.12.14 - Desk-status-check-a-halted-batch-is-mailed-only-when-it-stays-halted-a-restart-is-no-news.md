---
id: TASK-345.12.14
title: >-
  Desk status check: a halted batch is mailed only when it stays halted, a
  restart is no news
status: Done
assignee: []
created_date: '2026-10-05 12:15'
updated_date: '2026-10-05 12:16'
labels: []
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 296000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05: no notice that a mailing was interrupted and none that it runs again; a status check instead, and a mail only when something stays broken with an error ("как в центре"). Before, every halt mailed "рассылка прервана/остановлена" from the dying process and every resume "рассылка продолжается", so each restart cost mails.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A batch process only logs its halt (and resume) in the ledger and mails nothing; announcements, round reports and "рассылка завершена" are unchanged
- [x] #2 The desk checks every poll: a halt still in force after halt_grace_minutes (desk config, 15) with no halt notice after it is mailed once to the notify list with its reason (error: how to resume, delivery: new plan and approval), logged as a notice with halt_ts
- [x] #3 A halt resumed within the grace, and a halted batch whose letters a later batch of the campaign carries, are never mailed
- [x] #4 tests cover: a restart inside the grace sends nothing, a halt that stays is mailed once, a superseded batch is not mailed, halt_notice text per kind
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
05.10: tools/clinic_mailer.py send_scheduled logs halt and resumed without mail; halt_notice and unnoticed_halts; tools/daria_desk.py status_check each poll, desk config halt_grace_minutes 15. Live slip on deploy: before the "batch carried by a later plan" rule the check mailed two halts of the replaced 1218 batches (13:37) once; the rule was added and a dry run over both ledgers returns nothing. Not covered: a process killed without a halt event (SIGKILL, out of memory) leaves no trace for the check. Tests: 56 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A halt and a resume are only logged; the desk mails a halt once when it is still in force after 15 minutes and no later plan carries its letters. Verified by tests (restart inside the grace, halt that stays, superseded batch) and a dry run on the live ledgers.
<!-- SECTION:FINAL_SUMMARY:END -->
