---
id: TASK-173
title: >-
  Verify step ignores the crawler's per-host User-Agent override: RHÖN Bad
  Neustadt postings 401 over plain HTTP and escalate to paid Firecrawl every
  night
status: Done
assignee:
  - '@claude'
created_date: '2026-09-29 21:30'
updated_date: '2026-09-29 21:55'
labels:
  - verify-freshness
dependencies: []
priority: medium
type: bug
ordinal: 171000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up 1 of TASK-168, approved by Ivan 2026-09-29 (via coordinator). bewerberportal.rhoen-klinikum-ag.com (RHÖN-KLINIKUM AG rexx board; the 18 open nursing postings of RHÖN-KLINIKUM Campus Bad Neustadt, clinic 67308) answers HTTP 401 with an empty body to any User-Agent claiming Chrome and 200 to Firefox. TASK-168 taught the crawler this through `crawlers/vendor_adapters.UA_OVERRIDE` (a per-host UA table read only by `vendor_adapters.get()` via `_headers_for`), but the verify step (`pflege_jobs/verify.py`) sends its own constant Chrome/126 `UA` in both `verify_url()` and the http rung of `verify_one()` and never looks at that table.

Effect: every nightly verify (app/crawl.py `_run_verify` -> `verify_all`) gets 401 -> blocked on the http rung for all 18 postings, the Playwright rung (also a Chrome UA) sees an empty page -> "200 but title not found", and the ladder escalates each one to the Firecrawl rung -- a paid scrape per posting per day (TASK-168 notes: the 05:17 verify had all 18 "live ... [firecrawl]"). Without Firecrawl (run-end `_verify_ids`, firecrawl=False) they land on verify_status=error: live DB 2026-09-29 21:30 UTC, all 18 open 67308 postings verify_status=error, verify_http=200, note "200 but title not found (JS-rendered or list page)", verified_at 17:35:41 (TASK-168 re-crawl run 916701).

Live re-check 2026-09-29 21:27 UTC: stellenangebote.html -> crawler UA Chrome/125 401 0 B, verify UA Chrome/126 401 0 B, Firefox/128 200 106238 B; posting page j659 -> Chrome/126 401 0 B, Firefox/128 200 34264 B with the posting title in <title>.

Constraint from Ivan: one source of truth, no second UA table. Layering: nothing in pflege_jobs/ imports crawlers.vendor_adapters today, and vendor_adapters already imports pflege_jobs.verify lazily (WALL_MARKERS/_bounced_to_list, _EINSATZORT/_PLZ_ORT/_clean_city/_placeable), so verify importing vendor_adapters would be a new edge and a cycle.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 verify.py http rung (verify_one) and verify_url send the per-host override UA for an overridden host and verify.py own default UA for every other host; an offline test proves both and fails on the old code
- [x] #2 The override table and its host lookup exist exactly once, imported by both crawlers/vendor_adapters.py and pflege_jobs/verify.py, with no pflege_jobs -> crawlers.vendor_adapters import and no import cycle
- [x] #3 Mutation test: breaking the verify-side lookup turns the new test red; restore from the /tmp copy is byte-identical and green; non-network suite 0 failed
- [x] #4 Live: the 18 open postings of clinic 67308 come back live from the http rung (method=http, no Firecrawl rung) with the fixed code; before/after verify_status counts recorded
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Move UA_OVERRIDE + the host lookup out of crawlers/vendor_adapters.py into a new pflege_jobs/user_agent.py (same shape as pflege_jobs/posting_signal.py, TASK-123: one shared module the crawl layer and pflege_jobs both import). crawlers -> pflege_jobs is the existing direction (section, posting_signal, sources.beesite/hr4you at module level), so no new edge and no cycle.
2. vendor_adapters: import UA_OVERRIDE (kept as a module attribute, tests read va.UA_OVERRIDE) + ua_override from it; _headers_for keeps its behaviour (override UA + Accept-Language, else H).
3. verify.py: both session.get calls (verify_url, verify_one http rung) send ua_override(url) or verify.UA. Default UA unchanged (Chrome/126), Playwright/Firecrawl rungs untouched.
4. RED test first in tests/test_verify_escalation.py (fake session records the UA; RHÖN host -> Firefox override, other host -> verify.UA). Mutation test, full non-network suite.
5. Live dry run: verify_all(render default, firecrawl=False) on the 18 open 67308 postings (v_postings rows, as _run_verify builds them), old code vs new code, no push; record counts.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation (worktree /home/exedev/repo/.claude/worktrees/grpUA, branch grpUA = HEAD 14cacc4 + main tree uncommitted diff /tmp/grpUA/base.patch (1103 lines, sha256 1a7ca04a...) + untracked tests/fixtures copied; NOT committed)
Layering checked first: pflege_jobs/ imports crawlers.* only lazily and only crawlers.portals (verify.py render/board_titles/_close_browser, l.267/326/588); nothing in pflege_jobs imports crawlers.vendor_adapters; vendor_adapters already imports pflege_jobs.verify lazily (l.957 WALL_MARKERS/_bounced_to_list, l.3176 _EINSATZORT/_PLZ_ORT/_clean_city/_placeable) and pflege_jobs.section/posting_signal/sources.beesite/sources.hr4you at module level. So verify -> vendor_adapters would be a new edge AND a cycle -> table moved instead.
- NEW pflege_jobs/user_agent.py: UA_OVERRIDE (both entries + their TASK-114/TASK-168 evidence comments, moved verbatim) + ua_override(u) -> the listed UA for the host (exact domain or subdomain, same rule as the old _headers_for loop) else None. urlparse(u or "") so a missing url stays "no host" (urlparse(None) returns bytes and .endswith(str) raised TypeError; before this change verify_url(None) returned ("error", None, "MissingSchema") and still does -- checked).
- crawlers/vendor_adapters.py: table removed; `from pflege_jobs.user_agent import UA_OVERRIDE, ua_override` (UA_OVERRIDE re-exported: tests/test_vendor_adapters.py reads va.UA_OVERRIDE); _headers_for = override UA + Accept-Language if listed, else H (unchanged behaviour).
- pflege_jobs/verify.py: `from .user_agent import ua_override`; verify_url and verify_one http rung send `ua_override(url) or UA`. verify.UA (Chrome/126) stays the default; Playwright rung (crawlers.portals Chrome/125, same as the crawler own Playwright path) and Firecrawl untouched.
Other worktrees (grpB1/B2/B3/CLS/G/V/ledger) carry the identical UA_OVERRIDE block as main (md5 of the block equal), so no other pending patch edits the moved table.

## Tests (tests/test_verify_escalation.py, new section TASK-173)
- test_verify_http_rung_sends_the_crawlers_user_agent_for_an_overridden_host: a fake session that answers like the RHÖN board (401 + empty body to any UA containing "Chrome", posting page otherwise, records UAs); crawlers.vendor_adapters.get() and verify_one(rungs=("http",)) + verify_url on the same j3105 URL -> crawler sent [Firefox/128], verify sent [Firefox/128, Firefox/128] (literal UA), verdict ("live", 200, "http").
- test_verify_http_rung_keeps_its_own_user_agent_for_every_other_host: www.rhoen-klinikum-ag.com (same operator, sibling host, not overridden) -> verify sends [verify.UA, verify.UA].
RED on old verify.py: `AssertionError: assert (blocked, 401, http) == (live, 200, http)` (test 2 passes on old code by design; its teeth are M3/M4).
Mutations (file copied to /tmp/grpUA/mut/*.orig, broken, run, restored FROM the copy, __pycache__ removed, re-run, diff -q identical -- all 4 identical, 2 passed after each restore):
 M1 verify_one http rung sends UA -> FAILED ..._for_an_overridden_host (blocked 401)
 M2 verify_url sends UA -> FAILED ..._for_an_overridden_host
 M3 lookup matches the parent domain (`domain.split(".",1)[-1] in host`) -> FAILED ..._for_every_other_host
 M4 override applied to every host -> FAILED ..._for_every_other_host
Related suites green: test_verify_escalation + test_vendor_adapters + test_completeness_rexx 117 passed.

## Live proof (dry; /tmp/grpUA/live_verify.py = the _run_verify row build from app.data.jobs()/towns() + verify_all(workers=8, firecrawl=False); NOTHING pushed)
Before, live DB (pflege_jobs.postings, read-only session, 2026-09-29 ~21:30 UTC): 18 open postings clinic 67308, verify_status error=18, verify_http 200, note "200 but title not found (JS-rendered or list page)", verified_at 17:35:41 (TASK-168 run-end verify). Snapshot /tmp/grpUA/before_67308.json.
Old code (main-tree pflege_jobs/verify.py, render off): "http pass done: 0 decided, 18 need a browser"; blocked 401 http = 18.
Fixed code (worktree, render on as nightly, Firecrawl off): "http pass done: 18 decided, 0 need a browser", 0.8 s; live 200 http = 18 (notes title tokens 3/3, 2/2 or 1/1; page JSON-LD city "Bad Neustadt an der Saale" on all 18). Output /tmp/grpUA/live_old_code.txt, /tmp/grpUA/live_new_code.txt.
Not pushed (DB writes are Ivan-approved per write): the next scheduled verify writes live via the normal EdgeSink verify op once the patch is merged, with 0 Firecrawl scrapes for these 18.

Validation (worktree grpUA, env sourced, __pycache__ removed first): `.venv/bin/python -m pytest -p no:cacheprovider -m "not network" -q` -> 1581 passed, 18 skipped, 2099 deselected, 0 failed (7:19); covers TASK-173 + TASK-176 together. Import check: `import pflege_jobs.verify` loads no crawlers.* module; crawlers.vendor_adapters.UA_OVERRIDE is pflege_jobs.user_agent.UA_OVERRIDE (same object); _headers_for returns H itself for a non-overridden host. Patch of ONLY these changes (4 files: crawlers/vendor_adapters.py, NEW pflege_jobs/user_agent.py, pflege_jobs/verify.py, tests/test_verify_escalation.py; +142/-23): /tmp/grpUA/ua.patch (sha256 e14c1bad...), == worktree `git diff`, `git -C /home/exedev/repo apply --check` OK at 21:54 UTC. NOT applied to the main checkout, NOT committed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The verify step sent its own Chrome/126 User-Agent to every host and never read the per-host UA table the crawler uses. bewerberportal.rhoen-klinikum-ag.com answers 401 to any Chrome UA and 200 to Firefox (checked live again 2026-09-29), so it refused all 18 open RHÖN Bad Neustadt postings (clinic 67308). Each was blocked on the HTTP rung and rendered blank in Chromium. Each night it then cost a paid Firecrawl scrape, or ended as verify_status=error when Firecrawl was off.

Fix: UA_OVERRIDE and its host lookup moved unchanged from crawlers/vendor_adapters.py into a new pflege_jobs/user_agent.py (ua_override). vendor_adapters._headers_for, verify_url and the HTTP rung of verify_one now all call it. For every other host, verify keeps its own default UA. The table moved rather than being imported from vendor_adapters, because that import would add a new pflege_jobs -> crawlers dependency and an import cycle (vendor_adapters already imports pflege_jobs.verify).

Verified:
- 2 offline tests; on the old code verify returned blocked 401 where live 200 was expected.
- 4 mutations each failed the tests, then the byte-identical restore passed.
- Non-network suite: 1581 passed, 0 failed.
- Live dry run on the 18 postings (Firecrawl off, nothing pushed): the old code returned blocked 401 for all 18; the fixed code returned live 200 over plain HTTP for all 18 in 0.8 s. The DB still shows error for all 18.

The patch is /tmp/grpUA/ua.patch (shared with TASK-176) and is not yet applied to main. After the merge, the next scheduled verify writes live for these postings with no Firecrawl scrapes.
<!-- SECTION:FINAL_SUMMARY:END -->
