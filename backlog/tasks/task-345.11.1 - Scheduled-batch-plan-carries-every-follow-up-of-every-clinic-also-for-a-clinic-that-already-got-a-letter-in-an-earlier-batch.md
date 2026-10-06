---
id: TASK-345.11.1
title: >-
  Scheduled batch plan carries every follow-up of every clinic, also for a
  clinic that already got a letter in an earlier batch
status: To Do
assignee: []
created_date: '2026-10-02 10:57'
updated_date: '2026-10-05 10:20'
labels: []
dependencies: []
parent_task_id: TASK-345.11
priority: high
ordinal: 288000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-02: "все fu должны быть всегда включены в план волны". Wave 2 (nurse79-2) halted on 02.10 after one letter, to Klinik Neustadt a.d. Aisch, because the desk died on a Graph 404 and the batches halted on the stale heartbeat. The re-plan for the 19 clinics that had not been written to left Neustadt out: `plan` skips a step that is not due yet (`now < due`), and `timed()` refuses a scheduled batch whose clinics start at different steps ("a scheduled batch starts every clinic at the same step"). Neustadt's fu1 (Wed 07.10) and fu2 (Wed 14.10) therefore sat in no approved batch. Stopgap on 02.10: Ivan chose to plan them by hand as a separate mini-batch on Wed 07.10 after 11:32. The same gap hits any re-plan after a halt, and a wave planned in two parts.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A re-plan after a halt puts fu1 and fu2 of every clinic that already received a letter into the new scheduled batch, at their cadence times (previous send plus the step's after), together with the clinics that still start at the first step
- [ ] #2 A clinic whose next step is not due yet is planned, not skipped; send times stay inside the send window and keep the no-minute-divisible-by-5 rule
- [ ] #3 Round reports and the announcement PDF list the mixed-start letters correctly; the approval still covers every item by recipient and sha256 and refuses a batch that differs
- [ ] #4 tests/test_clinic_mailer.py covers: halt after one letter then re-plan carries that clinic's fu1/fu2 with the others' initial, plus the case where every clinic is at the same step (unchanged behaviour)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
05.10 evening, Ivan: a wave's approval covers its follow-ups; a follow-up plan needs no separate approval. First part done in tools/clinic_mailer.py plan(): for a scheduled batch (--announce-at) a step counts as due when it is due by --start-at, not by now, so a plan made on Monday for a Friday start carries the fu that falls due on Friday (test test_a_scheduled_batch_carries_a_follow_up_that_falls_due_before_its_start). Used the same day for wave 1 fu2 (batch nurse79-20261005-1218, Fri 09.10) and for fu1/fu2 of five wave 2 clinics (nurse79-2-20261005-1218, Thu 08.10 and 15.10). Still open: clinics at different steps in one batch (AC 1, 3, 4); until then follow-ups of clinics written to in an earlier batch run as a separate batch and process, one more process per campaign.
<!-- SECTION:NOTES:END -->
