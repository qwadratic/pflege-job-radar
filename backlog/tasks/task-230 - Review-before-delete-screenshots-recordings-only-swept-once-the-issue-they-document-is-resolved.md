---
id: TASK-230
title: >-
  Review before delete: screenshots/recordings only swept once the issue they
  document is resolved
status: Done
assignee: []
created_date: '2026-09-23 04:33'
updated_date: '2026-09-23 06:33'
labels: []
dependencies: []
project: whatsapp
ordinal: 177000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-23: 'давай просто не больше 2 недель хранить это и все, и перед удалением пересматривать чтобы убеждаться что удаляем либо хєппи, либо уже резолвнутые issue.' TASK-228's age-only sweep (SCREENSHOT_RETENTION_DAYS=14) deleted anything past the cutoff with no regard for whether it documented an unresolved problem. bridge/driver.py's sweep_screenshots/sweep_recordings (list+delete combined) are replaced with list_screenshot_candidates/list_recording_candidates (mechanical, mtime-only) + delete_paths (deletes exactly what it is given); new bridge/retention.py classifies each candidate as safe to delete (op done; or a failed send whose own outbound ledger row reached SENT/ABSENT/NOT_ATTEMPTED via a tick or bridge/executor.py::reconcile; or a failed op a human explicitly resolved via the new phone_ops.resolved_at / POST /v1/ops/<id>/resolve / tools/wa_bridge.py ops-resolve) or held (everything else, logged as a retention_held journal note, however old). Escalation shots (pre-TASK-227, no op_id in their filename) are tied to their send's client_msg_id via a new escalation_shot journal note written at capture time (bridge/executor.py::_escalate) and read back via ledger.escalation_shot_index(). /v1/reconcile, previously unreachable (no Client method, no CLI command, no HTTP wiring beyond a direct unqueued call), now goes through the ops queue like every other phone-touching route and has a Client.reconcile() + tools/wa_bridge.py reconcile command -- this is what lets a failed send's retention hold ever clear on its own. Found and fixed in passing: app/wa/bridge.py::Client._await_op had 'body.get("result") or {}', which would have silently turned reconcile's own empty-list answer into {}. WA_BRIDGE_DEBUG_CAPTURE now defaults ON (Ivan: 'там на макмини вроде, достаточно места') since the review gate makes it safe to leave running.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A screenshot/recording whose op finished OP_DONE, or whose failed op's own outbound ledger row later reached SENT/ABSENT/NOT_ATTEMPTED, or whose failed op was explicitly resolve_op'd, is deleted once past 14 days
- [x] #2 A screenshot/recording for a failed op with no resolution path yet (no client_msg_id, or its outbound row is still ATTEMPTING/UNCONFIRMED, or resolved_at unset) is held past 14 days, not deleted, and the hold is logged
- [x] #3 An old-style escalation shot (no op_id in its filename) is classified via its own send's outbound.state through a new escalation_shot journal trail, with the same delete/hold rule
- [x] #4 /v1/reconcile goes through the ops queue like every other phone-touching route, and is reachable from a human via tools/wa_bridge.py reconcile
- [x] #5 A failed op with no client_msg_id (clear_chat, delete_chat, read_thread, send_photos/gallery/document) can be marked resolved by a human via tools/wa_bridge.py ops-resolve
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Adversarial multi-lens review (Workflow, 4 Find + 5 Verify agents, wf_10b8e919-294) found 4 real safety gaps in the first cut, all fixed in bridge/retention.py before commit: (1) classify-then-batch-delete left every already-classified file exposed to a resend for as long as the whole sweep took -- fixed by deleting each file the instant it is classified, not batched at the end (_sweep_one_kind). (2) classify_op_artifact treated any OP_DONE as happy, but Executor.reconcile() is three-valued and never raises on 'indeterminate' -- a reconcile op could finish OP_DONE while the send it was checking was still wide open; fixed with _reconcile_settled(), holding unless every verdict closed the question. (3) same OP_DONE-is-happy assumption broke for send_photos/send_gallery/send_document, which never gate on a delivery tick the way send() does (TASK-131's own 'mechanism proof, not production ready' caveat) -- fixed with _media_send_confirmed(), reading the tick(s) back out of the op's own stored result. (4) classify_op_artifact fell through to the resolved_at manual-escape-hatch even when a client_msg_id existed and its outbound row was still open, so resolve_op (meant only for the no-client_msg_id kinds) could be misused to paper over an unresolved send -- fixed by returning on the client_msg_id branch unconditionally, never falling through to resolved_at when a client_msg_id is present. 8 new regression tests added (tests/test_bridge_retention.py), 2 existing ones updated for the now more specific hold reasons. Full non-llm suite + the four directly-touched lanes re-run clean after the fixes.

Correction (2026-09-23 06:35 UTC): the final summary first said '31 tests' for tests/test_bridge_retention.py -- the real count is 23. Miscount on my side, no change to what is covered.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Replaced TASK-228's age-only screenshot/recording sweep with a review-before-delete gate (bridge/retention.py): an artefact is deleted past 14 days only if its op finished OP_DONE and (for reconcile/send_photos/send_gallery/send_document, which can reach OP_DONE without confirming anything) the result itself confirms it, or a failed send's outbound row later reached SENT/ABSENT/NOT_ATTEMPTED, or a human resolve_op'd a failed op that never minted a client_msg_id. Everything else is held indefinitely and logged (retention_held), never guessed at -- WA_BRIDGE_DEBUG_CAPTURE now defaults on since the gate makes that safe. Wired /v1/reconcile (previously dead code) through the ops queue with a new Client.reconcile()/tools/wa_bridge.py reconcile, and added POST /v1/ops/<id>/resolve / tools/wa_bridge.py ops-resolve as the manual escape hatch. An adversarial multi-lens review workflow then found and I fixed 4 real safety gaps in the first cut (see implementation notes): a classify-then-batch-delete TOCTOU race, OP_DONE wrongly treated as happy for reconcile/media-sends, and resolve_op able to override a still-open send. Verified: tests/test_bridge_retention.py (31 tests, all 5 ACs covered directly), full non-llm/non-network suite clean (3060 passed, 35 pre-existing failures confirmed unrelated via git stash -- identical failures on the pre-TASK-230 baseline).
<!-- SECTION:FINAL_SUMMARY:END -->
