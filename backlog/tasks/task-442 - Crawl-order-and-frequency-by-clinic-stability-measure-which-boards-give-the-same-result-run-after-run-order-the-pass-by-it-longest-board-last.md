---
id: TASK-442
title: >-
  Crawl order and frequency by clinic stability: measure which boards give the
  same result run after run, order the pass by it, longest board last
status: To Do
assignee: []
created_date: '2026-10-06 11:06'
updated_date: '2026-10-06 11:27'
labels:
  - crawler
  - scheduler
dependencies: []
priority: medium
ordinal: 319000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06: the crawler should be dynamic: always somewhere, updating something, and run more often than the one nightly pass (schedule 1, about 643 clinics, 03:00 to about 09:45 UTC; one deterministic long board holds the log silent for about 90 minutes from about 06:15). DECIDED (Ivan): the guarantee is a maximal visit age of ONE DAY per clinic; there is no guaranteed share per run. A clinic whose last visit is older than 24 hours is a recorded, visible breach (named, listed), never a silent lateness. Analysis first, read-only, from the run history (crawl_runs rows, run logs, posting observations and content hashes across the runs available, e.g. 231, 233, 237): per clinic and board (a) the time cost per run and its spread, (b) how often the result repeats unchanged and how often it differs (postings added, removed, changed). Then: (1) selection and order by priority: clinics whose result changes often are visited more often than once a day, stable ones at least once a day; (2) the longest boards go last in their run, so everything else is in before they run; (3) per clinic the time of its last visit is recorded and visible; (4) the selection is recomputed from new run data, not kept by hand. Known today: a run that dies (a restart of pflege-web kills a running run, see TASK-435) leaves its clinics unvisited with nothing recording the gap; the age measure makes that visible. The service form is the crawl service of TASK-435 (one queued run at a time); check what app/scheduler.py supports for a clinic subset per run. The attribution chain of TASK-441 is the second reason: a posting that says which step produced it is easier to fix. Not in scope: adapter changes. No other task depends on this one.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Report per clinic and board: time per run (median, maximum) and change rate over the named runs; the 20 slowest and the 20 most volatile named; and today's age of the last visit per clinic with the number of clinics over 24 hours
- [ ] #2 Selection and order rule written down with the number of clinics per class; Ivan approves it
- [ ] #3 Rule implemented in the schedule or dispatch, red test first: every run records which clinics it covered; the age of the last visit per clinic is visible; a clinic over 24 hours is listed as a breach
- [ ] #4 After 7 days, before and after: oldest visit age (at most 24 hours, breaches named), time to the first result of the volatile boards, visits per day of volatile versus stable clinics
<!-- AC:END -->
