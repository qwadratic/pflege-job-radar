---
id: TASK-149
title: >-
  Crawl worker runs in-process, stale until a daemon restart -- inbox/link-cross
  already don't have this problem
status: To Do
assignee: []
created_date: '2026-09-24 10:50'
updated_date: '2026-09-25 00:10'
labels:
  - verify-freshness
  - infra
dependencies: []
ordinal: 149000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-24, after last night's crawl ran partly on stale code (the fix landed today but the daemon had been running since 2026-09-23 07:49): wants every crawl to always run on current on-disk code, without having to restart the main pflege-web daemon. Root cause, traced live: app/main.py:107 R.start_worker(CR.dispatch) hands app.crawl.dispatch a bare function reference; app/runs.py's crawl-worker thread (_loop/_executor) calls it IN-PROCESS inside the same long-lived uvicorn worker -- so crawlers/vendor_adapters.py, crawlers/routing.py, pflege_jobs/registry.py's Matcher, pflege_jobs/patterns.json (via classify.py, loaded once at import) all stay on whatever code was loaded at process start until a restart. This is NOT true of inbox/link-cross: app/crawl.py's own _cli() helper already shells out via subprocess.run([PYTHON, '-m', 'pflege_jobs.cli', ...]) for those two steps -- a fresh interpreter, so they already pick up current code every single run, no restart needed. Confirmed live 2026-09-24: last night's run 177 (390 clinics) had a link-cross statement-timeout at 00:05 (pre-TASK-124-fix code) but the SAME run's later link-cross call at 06:08 succeeded cleanly -- consistent with subprocess-based steps picking up my mid-session fix, while the in-process vendor-adapter/routing code stayed on the pre-session version for the whole run regardless. Recommended fix: make app.crawl.dispatch (or at least execute()'s adapter-fetching path) run the same way -- subprocess.run(['python','-m','app.crawl','dispatch', run_id]) mirroring _cli()'s already-proven pattern -- rather than a direct in-process call. Real risk surface to work out, not a one-liner: execute() is ~470 lines (698-1166) deeply coupled to app/runs.py's SQLite run-tracking (update_run/log calls throughout) via an in-process threading.Lock (R._lock) that would NOT be shared across a subprocess boundary -- SQLite's own file-level locking still protects against corruption, but the coordination/backoff behavior needs re-checking under real cross-process contention, and stdout/stderr streaming back into the run's own log (matching what _cli() already does for its 12-line tail) needs to carry the FULL run log, not just a tail, since this is the run's entire narrative, not a subprocess call's summary.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Design decision recorded: subprocess-ify dispatch()/execute() (matching _cli()'s pattern) vs. some other mechanism (e.g. importlib.reload() of the hot-path modules before each run -- weighed and likely rejected: reload() does not propagate through 'from x import specific_name' bindings used elsewhere in the codebase, e.g. crawlers/career_discover_exa.py's own import line, so a partial reload risks silently leaving some stale code active, worse than a clean restart because it is not obvious which part did not reload)
- [ ] #2 Cross-process coordination for app/runs.py's SQLite writes (update_run/log/create_run) verified safe under the chosen design -- either both processes go through the same lock via some IPC, or SQLite's own busy-timeout/WAL handling is confirmed sufficient with a live concurrency test
- [ ] #3 execute()'s full run log (not just a tail) reaches the run's own log table/UI when running as a subprocess, so a crawl run's log page is not degraded
- [ ] #4 Live-verified: editing a file execute() imports (e.g. crawlers/vendor_adapters.py) and firing a crawl run picks up the change with zero daemon restart
<!-- AC:END -->
