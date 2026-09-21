---
id: TASK-78
title: >-
  crawlers.portals shared Chromium singleton crashes on cross-thread reuse
  (Playwright sync API)
status: To Do
assignee: []
created_date: '2026-09-20 19:30'
labels: []
dependencies: []
priority: low
ordinal: 78000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-76 AC#2 stress-tested crawlers/portals.py's module-level _browser/_pw singleton (_get_browser()/_close_browser()) for thread-safety.

Reproduced live (2026-09-20): Playwright's sync API binds a browser connection to the OS thread that created it. When a second thread calls a method (new_context()) on a browser object crawlers.portals._get_browser() handed it from a DIFFERENT thread, it raises 'greenlet.error: Cannot switch to a different thread' and then a cascading TargetClosedError -- reproduced with a minimal script: init the browser in the main thread, then call browser.new_context() from a spawned thread.

_get_browser()'s own 'if _browser is None: ... _browser = ...' is also a plain, unlocked check-then-act: two threads racing the FIRST call can each launch their own Chromium and stomp the shared global, silently leaking one process's browser handle.

Today's call graph happens to avoid ever triggering this: pflege_jobs/verify.py's verify_all() explicitly documents and enforces a sequential (non-threaded) render pass specifically because of this constraint (its own ThreadPoolExecutor is used only for the HTTP rung), and app/runs.py serializes every crawl/verify run in this app through one background worker thread ('one crawl at a time keeps host politeness simple') -- crawlers/portals.py's own JS_PORTALS crawler (crawl_portals()) is not wired into crawlers/routing.py or app/crawl.py at all, so it only ever runs as a standalone CLI process with its own separate module state.

Nothing in the code enforces this invariant, though -- it holds only because every current caller happens to follow the same convention. A single new caller that touches the render rung, crawlers.portals.fetch_page(), or crawl_portals() from a second thread of the same process (e.g. a future synchronous 'quick verify' request handler, or wiring crawl_portals() into the scheduled worker) would start crashing intermittently with no test coverage to catch it, since the existing offline test suite exercises no real Playwright thread interleaving.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 _get_browser()/_close_browser() (or their call sites) make the single-thread-owner invariant explicit -- an assertion/lock/thread-id check that fails loudly instead of the current silent race, OR the risk is otherwise closed
- [ ] #2 A regression test (real threads, not just a docstring) demonstrates the fix prevents the crash reproduced in this task's investigation, or documents why no test is feasible without a live browser and pins the invariant a different way
- [ ] #3 No change to the existing single-threaded call graph's behavior/perf (verify_all's sequential render pass, the app's single crawl-worker) is required for this fix
<!-- AC:END -->
