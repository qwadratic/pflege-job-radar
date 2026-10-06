---
id: TASK-257
title: >-
  Debug artefacts of a failed send that never reached the ledger are held
  forever, and the operator escape hatch is explicitly powerless for them
status: Done
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 204000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/retention.py:106. Severity: operator-blind. 

HOW IT HAPPENS: A campaign is pacing. Every rail_parked 429 and every lost flock race on POST /v1/messages creates an op that fails before ledger.begin, with a client_msg_id in its args and no outbound row. Each leaves a 00_pre screenshot, a 02_error screenshot and up to 180 s of 720x1280 mp4. The nightly sweep classifies all three as hold/'outbound missing' forever; an operator who watches the recording and calls ops resolve changes nothing.

WHAT IT COSTS: The held pile grows monotonically on the mini for the single most common refusal on this rail. executor.last_retention['held'] is the signal meant to show a review backlog forming, and it becomes permanently non-zero and rising for artefacts no review can ever clear -- which is how a real backlog stops being noticeable. Disk on a shared home directory on the mini is the second-order cost.

PROPOSED DIRECTION (not a decision): Treat 'this op has a client_msg_id but the ledger has no row for it' as its own verdict rather than as an unresolved send: no row means ledger.begin never ran, which means nothing was typed, which is the safest state there is -- those artefacts are deletable on age alone. Keep the refusal to let resolve_op paper over a send whose outbound row exists and is still ATTEMPTING/UNCONFIRMED; that part is right. Separately, do not start capture for an op that is refused before the phone is touched -- moving the pre-shot and start_recording to after take_phone (which also helps finding 3) means a 429 stops costing a full screen recording of nothing.

VERIFICATION NOTES: CONFIRMED end to end. executor.send calls validate_send, ledger.classify, governor.check and take_phone all BEFORE ledger.begin (executor.py:139-155, with its own comment 'Step 4, and it is before begin on purpose'), so a rail_parked 429, a device_unavailable 503 or a validation refusal leaves no outbound row. The op args for a send are `{'req': <the posted body>}` (server.py:192), so classify_op_artifact's `((row.get('args') or {}).get('req') or {}).get('client_msg_id')` does find the id -- and _body() guarantees req is a dict, so this branch is always taken for a failed send. ledger.get then returns None and retention.py:106 returns ('hold', 'outbound still missing') unconditionally; the `if row.get('resolved_at')` fallthrough at line 107 is below it and unreachable for any op carrying a client_msg_id, so POST /v1/ops/<id>/resolve genuinely changes nothing for these. Debug capture is on by default (server.py:502: WA_BRIDGE_DEBUG_CAPTURE defaults to '1'), and dispatcher.run_one takes the pre-shot and starts the recording before the method runs, so a refusal that never touched the phone still produces two PNGs and a pulled mp4. The refusal's own note in the docstring ('never fall through to resolved_at here') is aimed at a send whose outbound row EXISTS and is open -- it over-applies to the case where there is no row at all.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Independently re-verified the sceptic's FIX conclusion against the code before touching anything (skeptic pass, no file edits during verification):
- Confirmed reachability: Executor.send runs classify -> governor.check -> take_phone, all before ledger.begin (bridge/executor.py:158-177). governor.check raises E.rail_parked (429) and take_phone raises E.device_unavailable (503), both pre-begin. errors.py's own table says "nothing was typed"/"nothing went out" for both.
- Confirmed dispatcher.run_one takes 00_pre screenshot + starts recording before calling the op method, for every kind (bridge/dispatcher.py:117-119), so a pre-begin refusal really does leave debug artefacts.
- Confirmed retention.py:98-106 hits entry=None unconditionally -> hold "outbound still missing" forever, no age check in that branch, and resolve_op is refused for anything carrying a client_msg_id (tests/test_bridge_retention.py:189-203).
- Confirmed the state-machine inconsistency: NOT_ATTEMPTED (a row that DOES exist, refused before any keystroke) is already in SAFE_OUTBOUND_STATES and gets auto-deleted; "no row at all" (strictly the same "nothing was typed" fact, just never written down) was held forever instead -- stricter than the state it's identical to.
- Confirmed via app/wa/store.py::claim_campaign_send (attempt = existing.attempt+1, reclaimable after any failed/uncertain attempt) and app/wa/bridge_ids.py::campaign_key (keyed on attempt number) that a campaign retry always mints a brand-new client_msg_id -- the orphaned key from a pre-begin refusal is guaranteed, not just likely, to never get a ledger row.
- Checked the one thing the sceptic didn't: whether entry=None could mean something OTHER than "begin never ran" (e.g. a row that existed and was purged later). bridge/ledger.py::sweep() only deletes outbound rows with resolved_at < now-30d (LEDGER_RETENTION_DAYS=30), while artefacts only become sweep candidates at 14d (SCREENSHOT_RETENTION_DAYS). 14 < 30, so within the window where classify_op_artifact first sees an artefact, entry=None can only mean no row was ever written -- confirms the fix is sound for the case this task describes.

CAVEAT for the record (not fixed here, out of scope for TASK-257): TASK-277 (already filed) documents that mark_unconfirmed also sets resolved_at, so a genuinely-uncertain UNCONFIRMED row (keys WERE pressed) also gets purged by the 30-day ledger sweep if a held artefact sits unresolved that long, and its own verification notes already name the consequence on this exact branch ("its artefact then holds on 'outbound missing' forever"). Before this fix that stayed a safe (if noisy) permanent hold. After this fix, once TASK-277's row-purge fires on such a row, classify_op_artifact will misclassify it as "delete: nothing was typed" -- wrong reason, and now destructive instead of merely noisy. This only manifests if TASK-277's own escape-hatch-closes-at-30-days bug fires first (an artefact held un-reviewed from day 14 to day 30+), so it doesn't block this fix, but TASK-277 should be read together with this change -- its own proposed direction (keep the phone_ops/outbound row alive while an artefact still names it) closes this gap too.

FIX IMPLEMENTED in bridge/retention.py::classify_op_artifact: split entry-is-None from entry-exists-but-open in the client_msg_id branch. entry is None now returns ("delete", "no outbound row: refused before ledger.begin, nothing was typed"), relying on the caller's existing age filter (list_candidates), same as every other delete verdict in this function. Updated the module's "THE THREE WAYS" docstring (AUTO-RESOLVED entry) to say why a row that will never exist belongs there. No changes to executor.py, dispatcher.py, ledger.py's write path, or any HTTP route.

TEST: tests/test_bridge_retention.py -- flipped test_a_failed_send_with_no_outbound_row_at_all_is_held to test_a_failed_send_with_no_outbound_row_at_all_is_deletable (asserts "delete", corrected docstring), and added test_review_and_sweep_deletes_an_artefact_from_a_send_that_never_reached_the_ledger (end-to-end through review_and_sweep, proving the sweep actually deletes such a file, not just that classify_op_artifact alone changes its verdict). Verified both new/changed assertions fail against the pre-fix code (git stash of retention.py alone) and pass with the fix.

Ran narrow suite only: .venv/bin/python -m pytest tests/test_bridge_retention.py -q -> 24 passed. Did not run the full suite (per standing instruction: lane tests during build, one full run at verification).

Left at In Progress; did not check acceptance criteria or move to Done -- that's the verification pass.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
bridge/retention.py:136-143 distinguishes a send refused before ledger.begin (no row ever written) from one still open, deleting the former on age alone instead of holding it forever; tests/test_bridge_retention.py:255 and :453 cover it end to end and both pass (34/34 in the file).
<!-- SECTION:FINAL_SUMMARY:END -->
