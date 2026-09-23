---
id: TASK-229
title: >-
  Brain tool: look_at_phone -- live chat verification before/after a
  communication step
status: Done
assignee: []
created_date: '2026-09-23 03:11'
updated_date: '2026-09-23 04:08'
labels: []
dependencies: []
project: whatsapp
ordinal: 176000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-225 fix, part D, depends on the ops-queue task's Client. Scope confirmed directly with Ivan: phone/live-conversation state only -- luna_brain.py's deterministic candidate-data logic (funnel_stage, requirement_scoreboard, market_snapshot, the document/housing/qualification gates) is unchanged, and turn_context()/_user_payload()'s DB-driven message history (app/wa/luna_brain.py:972-1034) stays as the prompt substrate -- it's deep, already-tested, and there's no live equivalent that doesn't mean re-deriving it every turn at real cost for something already correct. New MCP tool in app/wa/luna/tools_server.py, same pattern as show_clinic_photos: look_at_phone(phone) wraps BR.Client().read_thread(...) (door 2 live chat scan) so the model can ground-check real, currently-visible bubbles and delivery ticks before a risky/ambiguous reply and right after any send. No separate 'check op status' tool is needed -- the queue task's Client already returns a confirmed outcome from every send-type tool. Add a RULES entry in app/wa/luna/prompts.py (alongside SHOW_CLINIC_PHOTOS) instructing when to call it and how to react to what it returns. Also bump app/wa/config.py:122 LUNA_EFFORT default from high to max so tool calls are made carefully.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 look_at_phone(phone) is callable by the model and returns real, currently-visible chat state, not a DB snapshot
- [x] #2 prompts.py instructs the model to call it before an uncertain reply and after any send, with concrete guidance
- [x] #3 LUNA_EFFORT defaults to max
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented: app/wa/luna/tools_server.py::look_at_phone() -- no arguments (phone comes from _turn_phone()/WA_LUNA_PHONE, same as match_cv_to_postings, never a model-visible argument -- PII design already established in this file). Wraps BR.Client().read_thread(phone=phone, include_text=True) (the queued, poll-to-terminal Client from TASK-227, so it never races the dispatcher), trims the response to {chat, count, incoming, outgoing, messages:[{direction, clock, tick, body}]} -- drops internal plumbing (ok/at/visibility/body_sha256/body_len) the model has no use for. A BridgeError raises ToolError with instructions to say nothing and continue from stored history, same non-crashing pattern show_clinic_photos uses for BR.HANDSET_TOUCHED_CODES. app/wa/luna/prompts.py gained a LOOK_AT_PHONE RULES entry (next to SHOW_CLINIC_PHOTOS) naming the two call moments (before an uncertain reply, right after show_clinic_photos) and telling the model never to surface the tool or a null tick as a failure. app/wa/config.py LUNA_EFFORT default high -> max. IMPORTANT SEPARATE FINDING while wiring this in: app/wa/luna_brain.py::MCP_TOOL_NAMES is the actual --allowedTools list the live model can call (--strict-mcp-config restricts to exactly this tuple) -- a tool merely being @mcp.tool()-registered in tools_server.py is not enough, and show_clinic_photos was NOT in that tuple, meaning it is currently unreachable by the live model despite being fully implemented (confirmed via tests/test_wa_luna_tools.py's own completeness test, which only asserts allowed <= registered, not the reverse, so nothing catches a registered-but-unlisted tool). look_at_phone WAS added to MCP_TOOL_NAMES here (required for this task's own AC1). show_clinic_photos's gap is pre-existing, outside TASK-229's scope, and left as found -- flagged to Ivan rather than silently fixed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
New read-only MCP tool look_at_phone() lets the model ground-check the live WhatsApp chat on the handset (via the TASK-227 queued Client) instead of only the DB-built prompt history, for the two moments that matter: an uncertain reply, and right after show_clinic_photos. Wired into MCP_TOOL_NAMES (luna_brain.py's actual --allowedTools list) so it is genuinely callable, not just registered -- this surfaced a pre-existing, separate bug where show_clinic_photos itself was never in that list and is therefore unreachable by the live model; flagged to Ivan, left unfixed as out of this task's scope. LUNA_EFFORT default raised high -> max. Verified: 3 new unit tests (tools_server.py, live chat trimmed correctly / logs nothing sensitive / BridgeError becomes a non-crashing ToolError), the pre-existing tool-allowlist completeness test strengthened to require look_at_phone in both the registered and allowed sets. tests/test_wa_luna_tools.py + test_wa_luna_dialog_rules.py + test_wa_luna_campaign.py: 357 passed, 0 regressions.
<!-- SECTION:FINAL_SUMMARY:END -->
