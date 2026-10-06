---
id: TASK-434
title: >-
  Open candidates get a scheduled rematch and a one-page status report for the
  operator
status: To Do
assignee: []
created_date: '2026-10-06 07:51'
labels: []
dependencies: []
ordinal: 308000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-06: while a candidate has no job, rerun the clinic match on the live board: week 1 daily, week 2 three times a week (Mon/Wed/Fri), week 3 once, week 4 a final rematch plus a report. The daily step is a cheap diff of live ads against clinics not yet written; the heavy check (trip time, housing, second-agent verification) runs only for new finds. New clinics found are never mailed automatically: the list goes into the 17:00 digest and a wave needs Ivan's approval as always. After week 4 the candidate stays in the candidate pool; rematching resumes only when more than one such candidate is in the pool. Stop: contract signed or Ivan closes the candidate. The report goes to Ivan only; Ivan forwards it to the candidate himself. Report = one A4 page built from the campaign ledgers (clinics written, letters sent, answers, rejections, what the rematch found, what is next), details on a hosted link. The hosting belongs to the WA harness lane (wa-harness), not the frontend; the finished document goes into the repository for them. Requested by the WhatsApp/email lane (pflege-board-25), which edits the task afterwards.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The rematch cadence runs from one place with its state recorded
- [ ] #2 Every rematch run is logged with counts; failures fail loudly
- [ ] #3 The report numbers come from the campaign ledgers
- [ ] #4 The one-page report and a detail page render from the same data
- [ ] #5 No automatic live sends: new finds go to the digest and a wave needs Ivan's approval
<!-- AC:END -->
