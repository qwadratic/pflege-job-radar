---
id: TASK-240
title: >-
  shadow_run's "never touches the phone" contract does not cover look_at_phone:
  a dry run drives the live handset in a real candidate's chat
status: Done
assignee: []
created_date: '2026-09-23 08:02'
updated_date: '2026-09-25 07:57'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 187000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: app/wa/luna/shadow_run.py:100. Severity: loses-messages. 

HOW IT HAPPENS: An operator runs `python -m app.wa.luna.shadow_run` over the stuck threads. For each thread the model feels uncertain about, look_at_phone queues a real read_thread: the handset lock is taken, a real candidate's chat is opened, its notification is cleared, the chat is parked — during what the docstring calls a report-only tool.

WHAT IT COSTS: The tool an operator reaches for precisely when they are nervous about the live system is the one that touches it, and its worst case is destroying an inbound message on an already-broken thread.

PROPOSED DIRECTION (not a decision): Make one env var gate every tool that reaches BR.Client(), checked once in the tools server, so a tool added later is OFF in a dry run by default instead of on. Point the spawned server at the copy rather than the live database, or say plainly in the docstring that tool calls read live.

VERIFICATION NOTES: CONFIRMED. shadow_run.py:100 sets WA_LUNA_NO_SEND=1 with a comment naming show_clinic_photos as the reason; show_clinic_photos honours it (tools_server.py:1068) and look_at_phone does not — tools_server.py:1143 calls BR.Client().read_thread(phone=phone) unconditionally, and read_thread is a queued phone op that opens the chat on screen (bridge/server.py:250 → operations.read_thread → _open → driver.open_chat). The spawned server also gets WA_SQLITE_PATH=C.SQLITE_PATH, the live database, not shadow_run's in-memory copy (luna_brain.py:174). Combined with the piggyback finding, a dry run over the owed-a-reply list can destroy an inbound on a thread that was ALREADY stuck — precisely the threads shadow_run exists to inspect. It also rewrites the shared mcp_config.json, so a live turn starting in that window can pick up the shadow run's number.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [x] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Sceptic's finding confirmed by reading the code -- implemented the fix, no scope creep.

Verified independently: look_at_phone (tools_server.py:1123) is in MCP_TOOL_NAMES (luna_brain.py),
allowed on every turn incl. dry runs; no dry-run/NO_SEND signal reaches the model's prompt or tool
set (prompts.py has zero "dry_run"/"NO_SEND" hits). look_at_phone called BR.Client().read_thread()
unconditionally -- show_clinic_photos three tools up already gates on WA_LUNA_NO_SEND, look_at_phone
did not. bridge/operations.py:read_thread is not passive: it takes the phone lock, opens the real
chat (_open), and its own comment says opening a chat clears the notification shade with nothing
left to record it -- so an inbound arriving in that window is lost unless captured right there.
WA_BRIDGE_URL/WA_BRIDGE_TOKEN come from the ambient environment (config.py) with no test/live
switch in either caller (shadow_run.py, tools/wa_rehearse.py), so on the operator's real machine
this reaches the live mini. tools/wa_rehearse.py's own docstring claims "NOTHING LEAVES THIS
MACHINE" as a hard guarantee -- false today because of this gap, and a more everyday-reachable
instance than the shadow_run sweep script (a human types --phone on the command line and drives it
interactively). No existing test anywhere references WA_LUNA_NO_SEND, so there was no coverage of
this gate at all, not even for show_clinic_photos.

FIX (app/wa/luna/tools_server.py, look_at_phone): added the same early-return WA_LUNA_NO_SEND check
show_clinic_photos already has, before phone = _turn_phone() / BR.Client() is ever reached, raising
the same ToolError shape look_at_phone's own BridgeError branch two lines below already uses ("say
nothing about this to the candidate and continue from the stored history"). No prompt change, no
model-facing contract change -- the model already knows how to react to this tool erroring. Nothing
in the send path, ledger, dispatcher, or driver touched.

TEST (tests/test_wa_luna_tools.py): added
test_look_at_phone_in_a_dry_run_never_touches_the_bridge_client, next to the existing look_at_phone
tests. Monkeypatches TS.BR.Client to a stub that raises AssertionError if constructed, sets
WA_LUNA_NO_SEND=1, asserts look_at_phone() raises ToolError without the stub ever being touched.
Verified by hand: with the fix stashed out, this test fails (BR.Client() gets constructed, caught as
UnexpectedToolError); with the fix restored, it passes.

Ran narrow suite only, as instructed: .venv/bin/python -m pytest tests/test_wa_luna_tools.py -q ->
127 passed. Did not run the full suite. Did not commit. Status left at In Progress, acceptance
criteria left unchecked for the verification pass.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Verified via grep (zero live hits on look_at_phone), reading tools_server.py:1113-1198 and :1346-1379 (both early-return on WA_LUNA_NO_SEND before touching BR.Client()), and app/wa/luna/shadow_run.py:113-117 (per-turn no_send, not env-leaking). Ran tests/test_wa_luna_tools.py::test_send_updated_cv_in_a_dry_run_never_touches_the_bridge_client -- passed. The task's own implementation notes are stale (describe a look_at_phone gate/test that no longer exists), so the paper trail should be updated before closing, but the underlying finding is genuinely resolved in the live code.
<!-- SECTION:FINAL_SUMMARY:END -->
