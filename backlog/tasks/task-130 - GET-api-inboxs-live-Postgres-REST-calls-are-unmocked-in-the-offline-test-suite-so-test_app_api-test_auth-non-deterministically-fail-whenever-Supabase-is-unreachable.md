---
id: TASK-130
title: >-
  app/data.py's job_detail() and inbox_summary() call
  A.rest_get/rest_count/rest_get_all directly (bypass D's cache), so the offline
  test suite non-deterministically hangs/fails whenever Supabase is unreachable
status: Done
assignee: []
created_date: '2026-09-23 12:56'
updated_date: '2026-09-23 13:06'
labels: []
dependencies: []
priority: medium
type: bug
ordinal: 130000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Confirmed the mutation-tested root cause directly (2026-09-23): D.job_detail() (app/data.py:507,512) and D.inbox_summary() (app/data.py:539,540,558) are the ONLY two app/data.py functions that call A.rest_get/A.rest_count/A.rest_get_all per-request, bypassing the D snapshot-cache layer (D._snap, stubbed in every test fixture) that every other route reads through. Both are reachable from the offline suite: GET /api/jobs/{id} (job_detail) via test_app_api.py::test_problem_json_404, and GET /api/inbox (inbox_summary) via test_auth.py::test_owner_only_passes_after_login[GET-/api/inbox]. A live PFLEGE_TESTS_OFFLINE=1 pytest -q -m "not network" run during a sustained external Supabase outage (2026-09-23) hung/failed exactly these two tests out of 1448 -- reproduced locally by removing the A.rest_* stub from tests/test_app_api.py's client fixture: test_problem_json_404 hangs indefinitely (confirmed via -v streamed output, not just a timeout guess). Fixed by stubbing A.rest_count/A.rest_get/A.rest_get_all in the client/env fixtures of both test_app_api.py and test_auth.py -- this covers both call sites structurally since the stub is at the A.rest_* level, not scoped to one caller.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The client/env fixtures in tests/test_app_api.py and tests/test_auth.py stub A.rest_count/A.rest_get/A.rest_get_all so inbox_summary() never reaches live Postgres during the offline suite
- [x] #2 PFLEGE_TESTS_OFFLINE=1 pytest -q -m "not network" passes deterministically with the real Supabase host unreachable (verified by pointing SUPABASE_URL at a black hole or similar), not just when it happens to be up
- [x] #3 The new stubs are proven load-bearing: with A.rest_count/A.rest_get/A.rest_get_all monkeypatched away, GET /api/inbox's response still reflects the local SQLite queue (IB.counts()) untouched -- confirms the fixture change didn't just mask the route, it isolated the one live call it made
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Stubbed A.rest_count/A.rest_get/A.rest_get_all in the client fixture (tests/test_app_api.py:70-72) and env fixture (tests/test_auth.py:37-39). One stub at the A.rest_* level covers both call sites (job_detail, inbox_summary) without touching either caller. Added tests/test_app_api.py::test_api_inbox_never_touches_live_postgres -- gives each stub a distinctive return value and asserts the GET /api/inbox response is built from exactly those values. Mutation-tested the fix itself (not just the new test): backed up tests/test_app_api.py to /tmp, removed the three stub lines, reran tests/test_app_api.py -v -- test_problem_json_404 hung indefinitely (job_detail()'s unstubbed A.rest_get reaching the real, outage-affected Supabase host), confirming the stub is load-bearing for job_detail too, not just inbox. Restored from the /tmp backup (diff -q byte-identical), reran: 249 passed, 8 skipped, no hang. Scope check: grepped app/data.py for every A.rest_(get|count|post|patch|delete) call site -- only job_detail and inbox_summary bypass D's cache per-request; the two at line 237-238 are inside refresh() itself, D.refresh is already stubbed wholesale in both fixtures, not a gap.
<!-- SECTION:NOTES:END -->
