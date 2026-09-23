---
id: TASK-283.4
title: 'Record and show the brains full turn: prompt, reasoning, tool calls, answer'
status: To Do
assignee: []
created_date: '2026-09-23 16:24'
labels: []
dependencies:
  - TASK-283.1
parent_task_id: TASK-283
priority: high
project: whatsapp
ordinal: 234000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan: "и размышления, и полные транскрипты как бы модели. То есть мозг видит, тулколы видит... прям вот визуализировать все, что там в мякотке это и происходит. Потому что это очень поможет при отладке".

This one is mostly a RECORDING task, not a rendering task, because the material does not exist yet.
Measured on the live VPS on 2026-09-23:

  * wa_luna_calls has exactly three columns -- id, phone, at. 213 rows. No prompt, no answer, no
    tool calls, no session id. It records that a turn happened and nothing about it.
  * data/wa_luna_sessions/tool_calls.jsonl exists and is appended by app/wa/luna/tools_server.py:162,
    609 lines. Every line is {"tool", "args", "at"} -- no phone, no turn id, no result, no duration.
    It cannot be joined to a conversation or to a turn, which is what makes it nearly useless for
    the debugging Ivan wants it for.
  * The model transcript itself lives in Claude Codes own session store, keyed by the session id in
    the thread card (_session_id) and resumed with --resume; nothing in this repo reads it back.

So the work is: persist a turn properly -- the system prompt and user payload luna_brain built, the
model answer, every tool call with its arguments AND its result and timing, the session id, and the
decision the turn produced -- and then show it next to the conversation it belongs to. Be careful
about size: board_snapshot.json handed to each turn is 7 MB, so whatever is stored must reference
inputs rather than copy them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A turn is stored with its prompt, its answer, its session id and the thread it belongs to
- [ ] #2 Every tool call is stored with the turn it belongs to, its arguments, its result and how long it took
- [ ] #3 The console shows a turn end to end next to the messages it produced
- [ ] #4 A failed or timed-out turn is stored and visible with the reason, not missing
- [ ] #5 Storage does not copy the board snapshot or other large inputs per turn
<!-- AC:END -->
