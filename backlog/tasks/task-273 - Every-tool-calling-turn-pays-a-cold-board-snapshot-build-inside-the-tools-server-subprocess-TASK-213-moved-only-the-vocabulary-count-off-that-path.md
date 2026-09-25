---
id: TASK-273
title: >-
  Every tool-calling turn pays a cold board-snapshot build inside the
  tools-server subprocess; TASK-213 moved only the vocabulary count off that
  path
status: Done
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 220000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna/tools_server.py:473. Severity: degraded. 

HOW IT HAPPENS: Any turn where the model calls a board tool: the child process builds the whole snapshot synchronously inside that tool call before returning a single row, on top of the parent's own model latency.

WHAT IT COSTS: A fixed 8-17s on the first tool call of every tool-using turn, which is the main reason the 120s budget in the timeout finding is tight at all; on a slow board day it is the whole turn, and the parent then kills the CLI leaving the child orphaned mid-build.

PROPOSED DIRECTION (not a decision): Hand the child the parent's warm board data the way the vocabulary is already handed over, or prime it before spawning. Failing that, bound the child's build well below the turn budget so a slow board surfaces as a tool error the model can route around instead of a turn-wide timeout.

VERIFICATION NOTES: CONFIRMED, with one detail corrected. The tools server is spawned fresh per turn, so app.data._snap (data.py:186) starts empty in it; the first board tool call (search_postings → _job_rows:393 → D.filter_jobs → snapshot) hits data.py:362 `if force or (empty and not loading): refresh()` — a SYNCHRONOUS full Supabase build (_build:253-291: v_postings + clinics + a second postings read), the same 8-17s TASK-213 measured and moved the vocabulary counting out of the child to avoid. The finder's "snapshot()'s wait parameter is 180s, longer than the turn budget" is WRONG for this process: the wait=180 branch (365) is only reachable when a build is already in flight in the SAME process, which cannot happen in a freshly spawned child. The cost is bounded by the REST calls' own timeouts, not by 180s. Everything else holds, including that _board_vocabulary_path's docstring ("This process refreshes its snapshot in the background and keeps serving the cached board") is true of the parent and not of the child that answers the tool call.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified the sceptic's chain by hand and agree it holds: fixed, not closed. app/data.py:snapshot() (362) does `if force or (empty and not loading): refresh()`; _snap (186) starts empty in the tools-server subprocess every turn, so search_postings/list_clinics/count_postings/match_cv_to_postings/get_posting all cold-build via D.filter_jobs/D.filter_clinics -> D.jobs()/D.snapshot() on their first call, same 8-17s TASK-213 measured. TASK-213's fix (board_vocabulary.py + luna_brain._board_vocabulary_path) only ever primed the tool-description text via WA_LUNA_BOARD_VOCABULARY; it never touched D._snap in the child, confirmed by reading tests/luna_fixture_tools_server.py, whose whole reason to exist is manually priming D._snap the production path had no way to do.

Fix (two files, no changes to app/data.py's state machine, nothing in bridge/ledger/dispatcher touched):
- app/wa/luna_brain.py: added _board_snapshot_path() next to _board_vocabulary_path(), dumping the parent's already-warm D._snap (jobs/clinics/by_clinic/facets/taxonomy -- same keys tests/luna_fixture_tools_server.py already dumps) to LUNA_SESSION_DIR/board_snapshot.json, and wired its path into _mcp_config_path's env dict as WA_LUNA_BOARD_SNAPSHOT.
- app/wa/luna/tools_server.py: added _prime_board_snapshot(), called from serve() before apply_board_vocabulary()/mcp.run() -- if WA_LUNA_BOARD_SNAPSHOT is set, loads the file and does D._snap.update(..., at=time.time(), loading=False, error=None), mirroring tests/luna_fixture_tools_server.py's own fixture-board priming exactly. snapshot()'s existing empty/stale checks then skip refresh() entirely; no variable (module run by hand) leaves the board cold-building as before.

Tests added in tests/test_wa_luna_tools.py (offline, no network, no handset):
- test_the_server_is_primed_from_the_parents_snapshot_and_never_builds_its_own: primes via WA_LUNA_BOARD_SNAPSHOT with A.rest_get_all patched to pytest.fail if called, then calls TS.search_postings and asserts real rows come back. Verified this fails with AttributeError before the fix (function didn't exist) and passes after.
- test_without_the_env_var_priming_is_a_no_op_and_the_cold_build_stays_available: guards the by-hand fallback -- D._snap is untouched when the env var is absent.

Ran narrowly: .venv/bin/python -m pytest tests/test_wa_luna_tools.py -q -> 135 passed. Also spot-checked tests/test_wa_luna_dialog_rules.py (173 passed) and the WA_LUNA_BOARD_VOCABULARY-adjacent tests in tests/test_wa_luna_brain.py -k 'vocabulary or mcp_config or tools_server or ready' (9 passed, 1 pre-existing failure confirmed present with or without this change -- a global D._snap test-isolation issue unrelated to TASK-273, left alone per scope). Did not run the full suite (per house rule: one full run happens in the owner's verification pass). Not committed; left status at In Progress for that pass.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC1 and AC2 genuinely satisfied. app/wa/luna_brain.py:160-173,240 and app/wa/luna/tools_server.py:412-423,1455-1461 implement the priming end to end; tests/test_wa_luna_tools.py:608 and :625 exercise both the primed and un-primed paths and pass, with the full test file (149 tests) showing no regressions.
<!-- SECTION:FINAL_SUMMARY:END -->
