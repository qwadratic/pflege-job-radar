---
id: TASK-432
title: >-
  Evaluate the new adapters on a full adapter pass: before/after against run
  233, per board and per field
status: To Do
assignee: []
created_date: '2026-10-06 07:40'
labels:
  - crawler-coverage
  - adapter-testing
dependencies: []
priority: medium
ordinal: 306000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06: judge how effective the new crawler is. Baseline: nightly adapter run 233 (2026-10-05, old adapters, commit 111e8f7: 21700 lines, 255 job-posting boards, 16285 job postings). New code: commit 3e891d0 and later (TASK-185/186 adapters: P&I position popup, Oracle CE REST for Sana, BITE place from custom field, place of the posting itself). Run 235 (night of 2026-10-06) was killed by a service restart at 07:33 UTC after 175 of 255 boards; on the 173 boards both runs share it showed no regression: 9902 vs 9875 postings, description filled 98.4 to 99.7 percent, loc 100 percent, employmentType 81.5 to 82.6 percent, datePosted 80.5 to 80.4 percent; 52 boards differ (AMEOS 734 to 694, Kreisklinik Woerth 11 to 1, LMU 159 to 152, Klinik Ebe 25 to 20, Schoen Klinik 296 to 291 and 294 to 291; kbo +6, TUM +4). The effect of the Sana Oracle and P&I changes is not visible yet. Work: take a full manual adapter run (started by the owner from the Pro panel, schedule Daily full pass, trigger run-now; record its run id here) and compare with run 233 and the earlier nights per board and per field (title, loc, description, employmentType, datePosted, org): counts, fill rates, posting identity (only in old, only in new), and name each drop as closed posting, adapter loss or different keying with evidence from the page. Separate the adapter effect from the day-to-day change by also comparing run 233 with run 231 (same old code, two nights). Not part of this task: any restart or deploy; other work does not wait for this run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Full manual adapter run id recorded in the notes with its commit_sha and duration
- [ ] #2 Per-board table old vs new (counts and field fill rates) with the 231-vs-233 night-to-night noise as the baseline
- [ ] #3 Every board that lost more than 5 percent or 3 postings is classified (closed posting, adapter loss, keying change) with evidence; adapter losses get a task
- [ ] #4 Effect of the Sana Oracle, P&I and BITE changes stated in postings and field fill rates
<!-- AC:END -->
