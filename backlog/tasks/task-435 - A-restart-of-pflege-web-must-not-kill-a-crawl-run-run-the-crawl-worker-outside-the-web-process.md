---
id: TASK-435
title: >-
  A restart of pflege-web must not kill a crawl run: run the crawl worker
  outside the web process
status: To Do
assignee: []
created_date: '2026-10-06 07:57'
labels:
  - infra
  - crawler-coverage
dependencies: []
priority: medium
ordinal: 309000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Verified 2026-10-06: the crawl worker is a thread of the web process (app/runs.py start_worker, executor crawl.dispatch). A restart of pflege-web (systemd KillMode control-group) kills the run and start_worker marks every row left running as failed with 'process restarted' (run 235: 4 h 33 min and 75 percent of the pass lost; Ivan ordered the restart, the loss was the price). Ivan wants the restart of the frontend service never to kill a run. Stopgap used for run 237: a backend run in its own transient systemd unit (systemd-run, same create_run + crawl.dispatch as the web worker), documented in docs/deploy.md. Proper fix: a separate crawl service (own unit, own process) that takes queued rows from crawl_runs and runs them; the web process only inserts a queued row and reads status; cancel via the existing cancel flag; a restart of the web service leaves the crawl service alone, a restart of the crawl service is the only thing that fails a running row; the deploy map (docs/deploy.md processes table and restart rule) updated; scheduled runs (schedules, verify after adapter) keep working. No second crawl at a time (host politeness stays).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A restart of pflege-web leaves a running crawl run running, shown by a test or a documented drill on the VM
- [ ] #2 The crawl service takes queued runs (schedule, run-now, API) and records status, log and commit_sha as before; cancel still works
- [ ] #3 docs/deploy.md processes table, restart rule and manual-run recipe describe the new state
<!-- AC:END -->
