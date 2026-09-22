---
id: TASK-116
title: >-
  Add app/wa/transport.get_client() and replace the seven M.Client()
  constructions
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 01:20'
updated_date: '2026-09-21 02:50'
labels:
  - wa-transport
dependencies:
  - TASK-115
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 124000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M2, the actual seam. This de-risks everything after it, because the injection point is then proven in production against the real Meta rail before any second transport exists.

Today `app/wa/` constructs `M.Client()` in seven places: `api.py` lines 303, 541, 923, 960; `luna/campaign.py` lines 1090 and 1134; `luna/import_history.py` line 533. A second transport cannot exist until those go through one factory.

`get_client(phone=None, **kw)` returns `meta.Client` or `bridge.Client`, chosen by the thread pinned rail and falling back to `C.WA_TRANSPORT`. In this task only the Meta rail is wired; `bridge` is a name that raises loudly until the bridge client exists.

No safety nets: `WA_TRANSPORT` is validated against the known values with a loud RuntimeError, exactly as the existing `WA_BRAIN` check does. An unknown value, or `bridge` before the bridge client exists, must raise. That is correct behaviour, not a gap.

Note `process_phones` (`api.py:565-574`) threads one client through for every phone, so per-phone rail resolution is a small signature change there, not literally a one-line swap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All seven M.Client() constructions are replaced by T.get_client(phone=...) and no module under app/wa/ constructs a transport client directly any more
- [x] #2 With WA_TRANSPORT unset or meta the harness behaves byte-identically to today
- [x] #3 An unknown WA_TRANSPORT value raises a loud RuntimeError at config load, naming the offending value; WA_TRANSPORT=bridge raises loudly while no bridge client exists
- [x] #4 process_phones resolves a client per phone rather than threading one client through the loop
- [x] #5 All 31 tests/test_wa_*.py files pass untouched; needing to edit one means the seam is in the wrong place
- [x] #6 readiness() reports the live transport so GET /api/wa/health shows which rail is serving without anyone reading .env
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add app/wa/config.TRANSPORT from WA_TRANSPORT, validated at import against ("meta","bridge") with a loud RuntimeError -- same discipline as WA_BRAIN. Report it from readiness().
2. Add app/wa/transport.get_client(phone=None, client=None, **kw): an injected client is returned as given (`is not None`, not truthiness); otherwise build meta.Client for "meta" and raise loudly for "bridge" until TASK-120 lands app/wa/bridge.py.
3. Replace all seven M.Client() constructions with T.get_client(...).
4. AC#4: stop submit_accepted pre-building a client before any phone is known; resolve one per phone inside process_phones and hand it to that phone's drain_pending.
5. Offline tests: the seam in isolation, an AST invariant that only transport.py builds a transport client, per-phone resolution in process_phones, and _send raising on an unbuilt transport.
6. Full offline suite with zero edits to any existing test file.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Files: app/wa/transport.py (new), app/wa/config.py (TRANSPORT + import-time validation + readiness), app/wa/api.py (4 sites), app/wa/luna/campaign.py (2), app/wa/luna/import_history.py (1), tests/test_wa_transport.py (new), docs/whatsapp.md, .env.example.

AC#4 was NOT a one-line swap and is worth reading before TASK-117. `submit_accepted` used to build the client in the request, before any phone was read, and thread it through process_phones -> drain_pending -> _send. Since get_client returns an injected client untouched, every `phone=` below that point would have been inert: one webhook payload carrying two candidates would have answered both over whichever rail the phone-less lookup picked. Fixed by building nothing in submit_accepted and resolving once per phone inside process_phones (api.py:581), which is also what makes the `phone=` arguments at api.py:304/935/972 real. Behaviour-identical on the Meta rail: meta.Client documents itself as one instance per request with no state of its own.

Still resolving without a phone, on purpose, and flagged in transport.py and docs/whatsapp.md: luna/campaign.py:1091 and :1135 (one run spans many numbers) and luna/import_history.py:534 (a media fetch). TASK-117 has to decide what a rail means for those two.

Verified: PFLEGE_TESTS_OFFLINE=1 .venv/bin/python -m pytest -q -m "not network and not llm" -> 1682 passed, 127 skipped, 70 deselected (149s). 32 tests/test_wa_*.py files, of which 31 pre-existing and unmodified (`git status --short tests/` lists only the new untracked file).

Per-AC evidence, all in tests/test_wa_transport.py:
- #1 test_only_transport_py_builds_a_transport_client walks the AST of every app/wa/**/*.py and asserts exactly one M.Client()/meta.Client() call exists in the whole package, in transport.py. AST, not grep, so a docstring naming M.Client() is not a false hit.
- #2 test_an_injected_client_is_handed_back_untouched / _is_never_replaced_by_a_live_one / test_the_meta_client_is_looked_up_on_the_meta_module_at_call_time (the existing suites patch app.wa.meta.Client; the seam must still go through that patch) plus the whole suite green unedited.
- #3 test_an_unknown_transport_stops_the_process_at_import and test_an_empty_transport_is_not_a_silent_default run a fresh interpreter (reloading config in-process would rewrite it for every other test in the session) and assert a non-zero exit naming the offending value; test_bridge_is_named_but_not_built_yet asserts the RuntimeError on bridge, matching behaviour text only, never a TASK id.
- #4 test_process_phones_resolves_one_client_per_phone asserts the resolution order is one call per phone, keyed on that phone; test_submit_accepted_builds_no_client_of_its_own fails the test if M.Client is constructed there at all.
- #6 test_readiness_reports_the_active_transport.
Also test_a_send_without_a_client_fails_loudly_on_an_unbuilt_transport: _send with client=None under WA_TRANSPORT=bridge raises instead of answering from the WABA number -- the seam proven reachable from a real send path, not only in isolation.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added app/wa/transport.get_client() as the single place an outbound WhatsApp client is built, replaced all seven M.Client() constructions with it, and added WA_TRANSPORT (validated at import, reported by readiness()). No safety nets: an unknown value stops the process at import and WA_TRANSPORT=bridge raises loudly until TASK-120 lands app/wa/bridge.py. AC#4 was implemented properly rather than nominally: submit_accepted no longer pre-builds a client before any phone is known, and process_phones resolves one per phone, which is what makes the phone= argument at the send sites real for TASK-117. Verified by tests/test_wa_transport.py (31 tests, including an AST invariant that nothing outside transport.py builds a transport client, per-phone resolution, and a real send path failing loudly on an unbuilt rail) and the full offline suite: 1682 passed, 127 skipped, with all 31 pre-existing tests/test_wa_*.py files unedited.
<!-- SECTION:FINAL_SUMMARY:END -->
