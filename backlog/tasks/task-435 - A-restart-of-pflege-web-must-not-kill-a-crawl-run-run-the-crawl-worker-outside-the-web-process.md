---
id: TASK-435
title: >-
  A restart of pflege-web must not kill a crawl run: run the crawl worker
  outside the web process
status: To Do
assignee: []
created_date: '2026-10-06 07:57'
updated_date: '2026-10-06 08:11'
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

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-06 implementation on branch feat/crawl-worker-service (not merged, not deployed, no service touched).
Done: app/crawl_worker.py = the crawl worker as its own process (python -m app.crawl_worker, unit deploy/pflege-crawl.service, Restart=always like the hunter unit). Loop: R.init(), mark rows left running as failed 'process restarted' (the only place that does it now), then drain queued rows oldest run_id first, one at a time through crawl.dispatch, sleep POLL_SECONDS=5, repeat. run_one claims a row with one atomic 'update ... where status=queued', so a row cancelled between pick and start is never started; last-resort failed handling as the old _loop. app/runs.py: _queue, _loop, start_worker removed; enqueue(run_id) stays as a documented no-op (create_run already inserts the queued row; call sites and the tests that stub R.enqueue stay valid). app/main.py startup: R.init() instead of R.start_worker(CR.dispatch), so a web restart never touches a row. Scheduler thread stays in the web process (schedules.fire -> create_run). Cancel is unchanged: queued -> cancelled by the API, running -> cancel_requested read by crawl.execute() from the table.
Found while tracing, fixed in the same change: crawl.kill_switch() (DISABLE tier) calls scheduler.pause(), an in-memory flag; run in the crawl process it would pause a scheduler that does not exist there and leave the web scheduler firing. The pause is now the settings row 'scheduler_pause' (scheduler.pause/resume/is_paused/status/tick read it). Side effect: a pause now survives a web restart (docs/coverage-plan.md already says 'until a human re-enables it').
Known, not changed: the board snapshot (data.refresh at the end of execute) is refreshed in the crawl process; the web snapshot catches up within data.TTL (10 min) or with POST /api/refresh-cache.
Tests: tests/test_crawl_worker.py (11): web startup leaves a running row running (red before, was 'failed'), enqueue leaves the row queued, worker takes queued row and keeps the executor's status, executor raising -> failed and the next row still runs, cancelled-while-queued skipped, cancelled between pick and start not started, worker startup fails stale running rows (and not queued/done ones), two queued rows run strictly one after the other, a row queued during a run is taken after it, scheduler pause shared through settings. Mutation checks on copies: executor not invoked -> 7 red; old runs.py/main.py back -> web-startup test red; all queued rows in threads at once -> never-together test red.
Open for the deploy (pflege-clawl): AC#1 drill on the VM and the switch, steps in docs/deploy.md 'Switching to the crawl service'. Order: nothing running (run 237's transient unit counts, the new unit would mark it failed), restart pflege-web, install and enable pflege-crawl. A queued row survives the switch, a running one does not. Task stays open until that drill is done; re-check owner: pflege-clawl.
<!-- SECTION:NOTES:END -->
