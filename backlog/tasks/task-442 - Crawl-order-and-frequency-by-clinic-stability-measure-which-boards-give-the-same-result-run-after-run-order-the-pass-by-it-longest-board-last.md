---
id: TASK-442
title: >-
  Crawl order and frequency by clinic stability: measure which boards give the
  same result run after run, order the pass by it, longest board last
status: To Do
assignee: []
created_date: '2026-10-06 11:06'
updated_date: '2026-10-06 11:11'
labels:
  - crawler
  - scheduler
dependencies: []
priority: medium
ordinal: 319000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06: the crawler should be dynamic: always somewhere, updating something. A pass in the minimal format takes several hours (schedule 1, about 643 clinics, 03:00 to about 09:45 UTC; one deterministic long board holds the log silent for about 90 minutes from about 06:15), so it should run more often, with each run guaranteed to cover a defined share of the clinics. Analysis first, read-only, from the run history (crawl_runs rows, run logs, posting observations and content hashes across the runs available, e.g. 231, 233, 237): per clinic and board (a) the time cost per run and its spread, (b) how often the result repeats unchanged and how often it differs (postings added, removed, changed). Then: (1) selection and order per run by priority: clinics whose result changes often come up more often, stable ones less often; (2) the longest boards go last in their run, so everything else is in before they run; (3) the share each run is guaranteed to cover is a written number, not a default; (4) per clinic the time of its last visit is recorded and visible, so a clinic that waits long is seen, not lost; (5) selection is recomputed from new run data, not kept by hand. Open for Ivan: the share per run, and whether a maximal age per clinic is wanted (nothing is invented as a cap here). The service form is the crawl service of TASK-435 (one queued run at a time); check what app/scheduler.py supports for a clinic subset per run. The attribution chain of TASK-441 is the second reason: a posting that says which step produced it is easier to fix. Not in scope: adapter changes. No other task depends on this one.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Report per clinic and board: time per run (median, maximum) and change rate over the named runs; the 20 slowest and the 20 most volatile named
- [ ] #2 Selection and order rule written down with the number of clinics per class and the guaranteed share per run; Ivan approves it
- [ ] #3 Rule implemented in the schedule or dispatch, red test first; every run records which clinics it covered and the age of the last visit per clinic is visible
- [ ] #4 After 7 days, before and after: age of the oldest visit, time to the first result of the volatile boards, share of clinics visited per day
<!-- AC:END -->
