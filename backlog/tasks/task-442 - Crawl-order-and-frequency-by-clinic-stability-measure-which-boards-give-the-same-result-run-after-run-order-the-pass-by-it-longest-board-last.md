---
id: TASK-442
title: >-
  Crawl order and frequency by clinic stability: measure which boards give the
  same result run after run, order the pass by it, longest board last
status: To Do
assignee: []
created_date: '2026-10-06 11:06'
labels:
  - crawler
  - scheduler
dependencies: []
priority: medium
ordinal: 319000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06. The nightly pass (schedule 1, about 643 clinics, 03:00 to about 09:45 UTC) visits clinics in one fixed order and one deterministic long board holds the log silent for about 90 minutes from about 06:15. Analysis first, read-only, from the run history (crawl_runs rows, run logs, posting observations and content hashes across the runs available, e.g. 231, 233, 237): per clinic and board (a) the time cost per run and its spread, (b) how often the result repeats unchanged and how often it differs (postings added, removed, changed). Then: (1) order the pass by it, the longest boards last so everything else is in before they run; (2) priority: clinics whose result changes often are checked more often (extra passes during the day); (3) the order is recomputed from new run data, not kept by hand. Open for Ivan: nobody is dropped from the full pass (no cap or skip invented); priority changes the order and adds passes for volatile clinics only. Extra passes need a schedule over a clinic subset: check what app/scheduler.py and the crawl service of TASK-435 support. Not in scope: adapter changes. No other task depends on this one.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Report per clinic and board: time per run (median, maximum) and change rate over the named runs; the 20 slowest and the 20 most volatile named
- [ ] #2 Order and frequency rule written down with the number of clinics in each class; Ivan approves it
- [ ] #3 Rule implemented in the schedule or dispatch, red test first; the nightly pass still covers every clinic
- [ ] #4 After 7 nights, before and after: time the pass ends and time to the first result of the volatile boards
<!-- AC:END -->
