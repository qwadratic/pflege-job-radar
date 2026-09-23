---
id: TASK-271
title: >-
  One show_clinic_photos call can exceed LUNA_TIMEOUT_SEC on its own, and the
  retry after the timeout can send the photos a second time
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 13:37'
labels:
  - rail-critique
  - degraded
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 218000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna_brain.py:864. Severity: degraded. 

HOW IT HAPPENS: The funnel's climax turn calls show_clinic_photos for a clinic with 3 photos on a slow board; the staging and the queued gallery send eat the turn budget; the CLI is killed after the album lands but before any text is produced. The candidate sees photos and no words. Three minutes later catch-up re-drives the same inbound.

WHAT IT COSTS: The most important turn in the funnel is the one most likely to time out, and its failure mode is photos with no accompanying text, then possibly the same photos again, then whatever the retry says — which reads as a broken bot exactly when interest peaks.

PROPOSED DIRECTION (not a decision): Take the slow parts off the model's critical path: the per-clinic photo set is identical for every candidate, so download-and-stage belongs in a cache keyed by clinic_id on the mini, not inside the turn. Size the turn budget against the tool that actually costs the most. And give send_gallery a deterministic key derived from (turn, clinic) before any retry can reach it.

VERIFICATION NOTES: CONFIRMED on the arithmetic, PLAUSIBLE on the duplicate. C.LUNA_TIMEOUT_SEC=120 (config.py:134) covers the whole turn. Serially inside one tool call: _fetch_clinic_expose 15s (tools_server.py:990), then per photo up to five times _download_to_temp 20s (1005) + ssh mkdir 20s (1024) + scp 30s (1029), then send_gallery through _request with no explicit timeout → budget = self.timeout = BRIDGE_TIMEOUT_SEC 90s (config.py:77) of _await_op polling behind the FIFO queue (bridge.py:475-500). Worst case is several hundred seconds for the tool alone; even a modest case (2 photos, warm) plus the cold snapshot build (next finding) plus effort=max thinking blows 120s. subprocess.run then kills the CLI, _live_reply raises "did not answer within 120s", process_owed_turn marks skipped_error (api.py:927-929) — with the gallery already delivered. The duplicate leg is plausible rather than proven: the card is not saved on the raised turn, so the same _session_id is resumed, and whether the resumed model sees its own prior show_clinic_photos call depends on how much of the killed CLI's transcript was flushed. send_gallery genuinely has no key, so nothing MECHANICAL stops the second send.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
VERDICT: the sceptic is right, this must be fixed. Verified both halves independently against the current working-tree code (not the prior VERIFICATION NOTES, which describe a pre-TASK-247 state: send_gallery now has an explicit GALLERY_BUDGET_SEC=187 timeout, not the bare 90s floor -- TASK-247 already raised the inner budget without ever checking it against the outer LUNA_TIMEOUT_SEC=120, which is exactly this finding).

CONFIRMED (budget): C.LUNA_TIMEOUT_SEC=120 (config.py) bounded the whole `claude -p` subprocess.run in luna_brain._live_reply; BR.GALLERY_BUDGET_SEC=187 (bridge.py:237-239) is send_gallery's own inner timeout for ONE empty-caption call, before tools_server.py's own _fetch_clinic_expose (15s) and up to 5x (_download_to_temp 20s + ssh mkdir 20s + scp 30s = 70s/photo) that run before it. 120 < 187 on the narrowest possible case.

CONFIRMED mechanism for the duplicate (sceptic's PLAUSIBLE half, made mechanical): subprocess.run(timeout=...) in _live_reply has no process-group handling (grepped, none exists) -- on timeout it kills only the `claude` CLI child, not the MCP tools_server.py grandchild spawned via --mcp-config, which is orphaned and keeps running any in-flight show_clinic_photos call to completion, including ST.record_outbound (tools_server.py) on the VPS's own sqlite. send_gallery/send_photos/send_document are explicitly documented (bridge.py, executor.py) as "MECHANISM PROOF, NOT PRODUCTION-READY ... no idempotency key ... calling it twice sends the photos twice" and bridge/executor.py never routes gallery/photos/document through the ledger's client_msg_id replay guard. show_clinic_photos's own docstring ("call it AT MOST ONCE per clinic") is a model-read instruction, not a gate; turn_context's outbound_since_last_turn is a soft, model-inferred signal, not a mechanical stop.

FIX IMPLEMENTED (two independent, code-local changes, no bridge.py/executor.py/ledger.py touched):
1. app/wa/config.py: LUNA_TIMEOUT_SEC default raised from a bare 120 to a derived 552s (15 expose-fetch + 5*70 worst-case per-photo download+stage + 187 GALLERY_BUDGET_SEC), same term-by-term discipline as DESTROY_BUDGET_SEC. Deviation from the sketch: a literal `from . import bridge` inside config.py was NOT used -- bridge.py already does `from . import config as C`, and importing bridge.py from config.py the other way round would be a real circular import (config.py is meant to be the leaf every other wa module depends on). Instead the terms are copied as literals with a comment explaining why, and a new regression test (tests/test_wa_luna_brain.py::test_luna_timeout_sec_exceeds_the_gallery_budget_it_wraps) imports both C and BR and asserts C.LUNA_TIMEOUT_SEC > BR.GALLERY_BUDGET_SEC, so the two cannot silently drift apart again. Noted trade-off in the comment: this also raises how long an ordinary, non-photo turn that is genuinely hung now waits before the turn is declared failed (120s -> 552s) -- inherent to one constant covering every turn shape, not new scope.
2. app/wa/luna/tools_server.py show_clinic_photos: added a DB-side guard right after the AUTOSEND/rail gates, before any download/stage/send -- queries ST.messages_for(conn, phone, direction='out') and short-circuits to {"sent": False, "reason": "already sent to this candidate"} if a prior row has meta.action=='show_clinic_photos' and meta.clinic_id==clinic_id (that meta shape is what ST.record_outbound already stamps on a real send, a few lines further down in the same function). New test: tests/test_wa_luna_tools.py::test_show_clinic_photos_does_not_resend_a_clinic_already_sent_this_conversation -- seeds that wa_messages row directly, then asserts _download_to_temp and BR.Client are never touched on the repeat call. Verified this test fails on the pre-fix code (AssertionError from the mocked _download_to_temp) and the LUNA_TIMEOUT_SEC test fails at 120 > 187 before the config.py change.

NOT DONE (deliberately, out of scope for this finding, matching the sceptic's own call): extending bridge/ledger.py's idempotency store to gallery/photos/document sends with a real deterministic key. That is a genuine cross-machine change (bridge/executor.py, bridge/server.py, app/wa/bridge.py's Client) and stays future work; the DB-side guard above closes the mechanical duplicate-send gap this finding is about without it. Also not addressed: LUNA_TIMEOUT_SEC is one fixed constant, so an unusually long researched caption could in principle still push GALLERY_BUDGET_SEC + caption-typing-time (bridge.py's own per-call formula) past even the new 552s ceiling -- inherent to a static top-level subprocess timeout and a separate concern from the mismatch this task names.

Tests run (narrow only, per instructions): .venv/bin/python -m pytest tests/test_wa_luna_tools.py tests/test_wa_luna_brain.py tests/test_wa_bridge_client.py -q -> 2 new tests pass, 1 pre-existing unrelated failure family (27 tests failing on PostgREST 401 / D.refresh network calls, reproduced identically on the pre-fix tree, unrelated to this change).

Status left at In Progress; acceptance criteria left unchecked, both fixes not committed, per instructions -- owner does one verification pass over the whole batch.
<!-- SECTION:NOTES:END -->
