---
id: TASK-229
title: >-
  Brain tool: look_at_phone -- live chat verification before/after a
  communication step
status: To Do
assignee: []
created_date: '2026-09-23 03:11'
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
- [ ] #1 look_at_phone(phone) is callable by the model and returns real, currently-visible chat state, not a DB snapshot
- [ ] #2 prompts.py instructs the model to call it before an uncertain reply and after any send, with concrete guidance
- [ ] #3 LUNA_EFFORT defaults to max
<!-- AC:END -->
