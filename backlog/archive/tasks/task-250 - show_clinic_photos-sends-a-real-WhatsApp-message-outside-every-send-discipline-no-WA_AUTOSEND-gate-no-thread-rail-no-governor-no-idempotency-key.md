---
id: TASK-250
title: >-
  show_clinic_photos sends a real WhatsApp message outside every send
  discipline: no WA_AUTOSEND gate, no thread rail, no governor, no idempotency
  key,
status: In Progress
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-26 08:49'
labels:
  - rail-critique
  - wrong-answer-to-candidate
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 197000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna/tools_server.py:1090. Severity: wrong-answer-to-candidate. A comment in the code already states this limit -- the question is whether that trade is still acceptable now the rail carries live conversations. 

HOW IT HAPPENS: Luna calls show_clinic_photos at 22:40. The governor would have parked a text bubble (outside the active window); the album goes out anyway, from the handset number even if the thread is pinned to Meta, with WA_AUTOSEND unset on a staging deployment, and with nothing in wa_messages. The candidate replies "schön!"; the next turn's outbound_since_last_turn shows the last recorded outbound, which is whatever text preceded the photos.

WHAT IT COSTS: A candidate can receive a photo album from an unexpected number, at a time the rail's own fuse forbids, past the daily cap, that the harness has no record of sending — and the model then reads their reaction as an answer to a different message.

PROPOSED DIRECTION (not a decision): Route the send through the same _send/get_client/begin_turn path every other outbound uses (that gets the rail, AUTOSEND, the key and the message row for free) and add a governor.check to the gallery route in the executor, or gate the tool off until that exists. At minimum the AUTOSEND gate and the wa_messages row are cheap and independent of the rest.

VERIFICATION NOTES: CONFIRMED on all four sub-claims. tools_server.py:1090 calls BR.Client().send_gallery(phone, ...) directly. api._send (api.py:964-1055) does the things it bypasses: T.rail_for/get_client (999-1000), the `if not C.AUTOSEND: record draft` branch (1006-1009), cl.begin_turn for the deterministic client_msg_id (1010-1021), and a wa_messages row per bubble. (a) rail: transport.rail_for (30-42) returns the thread's pinned rail, so a thread pinned to meta really does get photos from the handset number — plausible for pre-migration threads, not for a thread the phone rail already answered. (b) AUTOSEND: confirmed, the tool never reads C.AUTOSEND, so the documented "point a fresh deployment at the webhook without messaging anyone" mode still sends real photos. (c) governor: grep shows governor.check has exactly one caller, executor.py:151 inside send — gallery bypasses quiet hours/Sunday (governor.py:215), the per-number daily cap (225) and the gap floor; and because it writes no outbound row, ledger.count_spent does not see it, so it neither consumes quota nor advances the next slot. (d) no wa_messages row, so turn_context's outbound_since_last_turn (prompts.py:42,120) cannot show the model that photos went out. Already admitted: bridge.Client.send_gallery's docstring says "no idempotency key, no governor pacing check. Built and tested by hand, on one number, before wiring a gallery send into Luna's own automatic sends" — and it has now been wired in with none of them done, which makes the admission stale rather than an accepted trade.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Independently re-verified all sub-claims in the task's own verification notes against the code (not against the write-up): tools_server.py:1090 called `BR.Client().send_gallery(...)` unconditionally, no `C.` read anywhere in the file besides `SQLITE_PATH`/`LUNA_SESSION_DIR`; `MCP_TOOL_NAMES` gained `show_clinic_photos` today (2026-09-23) making the tool live, not hypothetical, with `prompts.py`'s rule already instructing the model to call it before the document ask, no consent/rail gate anywhere in `luna_brain.py`; `api._send` confirmed to apply four disciplines this path skips (rail_for/get_client, the AUTOSEND draft branch, begin_turn's idempotency key, a wa_messages row per bubble); `bridge/executor.py:send_gallery` confirmed to have no `governor.check` call at all and no `ledger.classify/begin/mark_sent`, only a `ledger.note` journal entry that `ledger.count_spent` (used by every cap/pacing check) never reads. All hold. No test anywhere currently exercises `show_clinic_photos`'s send path at all (only the `WA_LUNA_NO_SEND` dry-run branch is documented as tested, and even that claim was stale -- no test actually called it). Decision: FIX.

Implemented the CHEAP, LOCAL tier only, entirely inside app/wa/luna/tools_server.py's show_clinic_photos, plus one necessary companion line in app/wa/luna_brain.py (see #4). Did NOT implement the EXPENSIVE, SHARED tier (governor pacing + ledger idempotency in bridge/executor.py:send_gallery) -- left for a separate follow-up per the task's own tier split, since it touches the shared ledger SENT-state machine and governor.check's currently-single-caller contract.

Changes:
1. `if not C.AUTOSEND: return {"sent": False, "reason": "AUTOSEND is off"}` before staging/sending -- same gate api._send applies to every other outbound, same `{"sent": False, ...}` shape the tool's docstring already documents for the model.
2. Added a rail guard the sketch listed under "cheap tier" but its own later "blast radius" section re-bucketed as "expensive": refuse (same shape) unless `T.rail_for(phone) == "bridge"`. Judged this belongs in the cheap tier after reading the code -- `rail_for` is a read-only call into an already-tested function, touches no shared send path, no ledger, no api.py. Did NOT implement the sketch's alternative ("route through T.get_client" so a Meta-pinned thread gets Meta-rail photos instead) -- that is not implementable today at all: app/wa/meta.py's Client has no send_gallery method, so full dynamic routing would mean building new Meta-side capability, which is genuinely the expensive tier. A Meta-pinned thread is refused, not routed.
3. After a confirmed send, record a wa_messages row (kind="gallery", wamid=None since the bridge rail mints none for this call, body=the caption) via ST.record_outbound on a fresh ST.db() connection -- same fresh-connection pattern _send_reopen_template uses -- so outbound_since_last_turn (prompts.py) is no longer blind to a photo send.
4. Necessary correctness fix, not scope creep: tools_server.py runs as a fresh subprocess whose entire env is the dict luna_brain._mcp_config_path builds (documented in that function's own docstring as "not the parent's os.environ, is the subprocess's whole environment"); that dict never included WA_AUTOSEND. Without adding it, gate #1 would read C.AUTOSEND as False always in the subprocess regardless of the real deployment's WA_AUTOSEND, permanently disabling the tool rather than correctly gating it. Added "WA_AUTOSEND": "1" if C.AUTOSEND else "" to that env dict, same pattern as the existing WA_BRIDGE_URL/WA_BRIDGE_TOKEN passthrough.

Tests added:
- tests/test_wa_luna_tools.py: test_show_clinic_photos_refuses_to_send_with_autosend_off, test_show_clinic_photos_refuses_a_thread_already_pinned_to_the_meta_rail, test_show_clinic_photos_records_a_wa_messages_row_on_a_real_send.
- tests/test_wa_luna_dialog_rules.py: test_the_tools_server_is_told_whether_autosend_is_on (covers change #4, the env passthrough).
All four verified by hand to fail against the pre-fix code and pass against the fixed code (reverted each hunk, reran, restored).

Test results: tests/test_wa_luna_tools.py 130 passed; tests/test_wa_luna_dialog_rules.py 172 passed (ran both in full since luna_brain.py is shared). tests/test_wa_luna_brain.py has 27 pre-existing failures unrelated to this change (network: PostgREST 401 against a live Supabase, no credentials in this sandbox) -- confirmed via an A/B stash comparison that this diff neither adds nor removes any of that set (it incidentally left 2 fewer failing in the "with" run, but that's because stashing the whole file for the A/B check also reverted unrelated pre-existing TASK-239 work sharing luna_brain.py, not something this change caused). Did not run the full suite, per instructions -- narrow files only.

Left undone, flagged for the owner rather than filed as a new task (did not want to take that action unasked): the EXPENSIVE, SHARED tier -- governor.check + ledger idempotency (classify/begin/mark_sent) in bridge/executor.py:send_gallery, mirroring executor.send's sequence. Real and necessary, genuinely higher risk, deserves its own review and its own test against the ledger/governor state machine.

2026-09-26: folded into TASK-314 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
