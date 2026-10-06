---
id: TASK-434
title: >-
  Open candidates get a scheduled rematch and a one-page status report for the
  operator
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-06 07:51'
updated_date: '2026-10-06 07:54'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-06, first slice: the booklet of the 13 still unwritten clinics of the prepared wave 3 (docs/status/2026-10-06-wave3-booklet/, index.html plus a PDF, German, candidate-anonymous, self-contained). Layout convention from the WA harness lane: docs/status/<yyyy-mm-dd>-<slug>/index.html, optional PDF, later detail.html; no external fonts, scripts or images; the unguessable URL token is made at publish time and never goes into git.

First read-only rematch run, 2026-10-06 (about 10 s of compute): the 29.09 recheck scripts (build_universe, classify) do not run unchanged on today's board. department_hint is a list now (it was a string joined with a pipe), classify only knew wave 1 as written and fails on clinics missing from sb_facts.json and trips.json. A scheduled job needs a maintained version of them, with written clinics read from every campaign's recipients. The cheap funnel does not refresh mail history, housing, trip times or the liveness of ads on clinic sites; those were the expensive passes of 29.09 and belong to the heavy step for new finds only. Findings of the run: 6 of the 20 clinics that the 30.09 booklet draft labelled wave 3 had gone out in wave 2, so 14 were unwritten (13 with a live ad today); 2 new registry clinics; 7 old options lost their board ad.
<!-- SECTION:NOTES:END -->
