---
id: TASK-78
title: >-
  crawlers.portals shared Chromium singleton crashes on cross-thread reuse
  (Playwright sync API)
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 19:30'
updated_date: '2026-09-21 05:30'
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
- [x] #1 _get_browser()/_close_browser() (or their call sites) make the single-thread-owner invariant explicit -- an assertion/lock/thread-id check that fails loudly instead of the current silent race, OR the risk is otherwise closed
- [x] #2 A regression test (real threads, not just a docstring) demonstrates the fix prevents the crash reproduced in this task's investigation, or documents why no test is feasible without a live browser and pins the invariant a different way
- [x] #3 No change to the existing single-threaded call graph's behavior/perf (verify_all's sequential render pass, the app's single crawl-worker) is required for this fix
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Make the single-thread-owner invariant explicit in crawlers/portals.py: module _lock + _owner (the Thread object that launched the shared browser).
2. _get_browser(): acquire under the lock (closes the unlocked check-then-act race that let two threads each launch a Chromium) and refuse a non-owner thread with a RuntimeError naming the real constraint (Playwright sync API binds the connection to its creating thread) before any Playwright call is made -- fail loudly, no per-thread-browser fallback.
3. _close_browser(): same owner check (closing from a foreign thread raised greenlet.error into a bare 'except Exception: pass', which nulled the globals while the Chromium process stayed alive); clear _owner on close so ownership is free again afterwards.
4. New offline test tests/test_portals_thread_owner.py with REAL threads and a fake sync_playwright (no Chromium): (a) second thread calling _get_browser() raises, (b) N threads racing the first call launch exactly ONE browser, (c) _close_browser() from a foreign thread raises instead of silently nulling the globals.
5. Mutation-test: revert the fix, confirm all three go red; restore, confirm green. Then targeted tests + full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
IMPLEMENTED (crawlers/portals.py, +36/-14):
- Added module _lock (threading.Lock) and _owner (the Thread object that launched the shared browser).
- New _require_owner(): raises RuntimeError naming the real constraint (Playwright sync API binds a browser connection to its creating thread) when a non-owner thread asks. It runs BEFORE any Playwright call, so the failure is a clear message at the boundary instead of greenlet.error deep inside Playwright plus a cascading TargetClosedError that also kills the owner's connection.
- _get_browser(): the whole check-then-act now runs under _lock, closing the race where two threads each launched a Chromium and one handle leaked.
- _close_browser(): same owner check (a foreign-thread close raised greenlet.error straight into the existing bare 'except Exception: pass', nulling the globals while the Chromium process stayed alive), and clears _owner so ownership is free again after a close.

DESIGN CHOICE (per the project's no-safety-nets rule): refuse loudly, do NOT hand the second thread its own browser. A thread-local browser would silently multiply Chromium processes and quietly change what '_close_browser()' means for the caller that opened it -- it would hide the violation rather than surface it. A dead owner thread is likewise not recovered from: that connection is dead, and the refusal says so.

LIVE VERIFICATION (real Chromium, /tmp/live_thread_check.py: main thread takes the browser, a spawned thread calls _get_browser().new_context()):
- at HEAD (pre-fix): 'greenlet.error: cannot switch to a different thread' -- the exact crash this task reports.
- with the fix: 'RuntimeError: crawlers.portals shared Playwright browser belongs to thread MainThread and the sync API cannot be used from another thread; Thread-1 (run) must render in the owning thread ...', and the owner thread's next fetch_page() still works (about:blank rendered), i.e. the refusal does not poison the shared connection the way the greenlet crash did.

TESTS: tests/test_portals_thread_owner.py, 4 tests, real threads, fake sync_playwright (no Chromium, offline, fast).

MUTATION TESTING:
- full revert to HEAD: 3 failed, 1 passed -- second-thread refusal ('assert <_FakeBrowser> is None'), race ('4 browsers launched, expected exactly 1'), foreign-thread close ('expected a loud refusal, got None'). The 4th test (single-thread behaviour) passes pre- and post-fix on purpose: it is the AC#3 pin.
- lock removed only (_lock -> contextlib.nullcontext(), owner check kept): 1 failed, 3 passed -- only the race test, proving the lock is load-bearing on its own and not covered by the owner check.
- restored: 4 passed.

VALIDATION: .venv/bin/python -m pytest -m 'not network' -q -> 1243 passed, 1 skipped, 1195 deselected, 0 failed (334s). Targeted: tests/test_portals_thread_owner.py 4 passed; tests/test_portals.py, tests/test_verify_escalation.py (the only test touching crawlers.portals.fetch_page) green inside that run.

AC#3 evidence: the diff adds one uncontended Lock acquire per _get_browser()/_close_browser() call and changes no call site. verify_all()'s sequential render pass and app/runs.py's single 'crawl-worker' thread are untouched (git diff is limited to crawlers/portals.py + the new test file). Ownership is released on _close_browser(), so verify_all's own close at the end of its render pass leaves the next run -- in the same thread or a different one -- free to acquire. test_owner_thread_keeps_one_browser_and_close_frees_ownership pins exactly that and passes against BOTH the pre-fix and post-fix code, i.e. it proves the existing single-threaded behaviour is unchanged.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Made crawlers/portals.py's shared-Chromium single-thread-owner invariant enforced instead of conventional. _get_browser()/_close_browser() now run their check-then-act under a module lock (one browser, no racing double-launch) and record/verify the owning thread, refusing any other thread with a RuntimeError that names the real constraint -- Playwright's sync API binds a browser connection to its creating thread -- before Playwright is touched. Chose loud refusal over a per-thread-browser fallback: the fallback would hide the violation and silently multiply Chromium processes (no-safety-nets rule). Verified live with real Chromium: at HEAD a second thread's new_context() raises 'greenlet.error: cannot switch to a different thread'; with the fix it gets the RuntimeError and the owner thread's next fetch_page() still renders. Covered by tests/test_portals_thread_owner.py (4 tests, real threads, faked sync_playwright so it stays offline); mutation-tested -- full revert turns 3 of them red for the right reasons, removing only the lock turns the race test red on its own. Full offline suite: 1243 passed, 1 skipped, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
