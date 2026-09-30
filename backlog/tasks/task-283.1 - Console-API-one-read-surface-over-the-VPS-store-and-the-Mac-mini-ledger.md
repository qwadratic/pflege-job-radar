---
id: TASK-283.1
title: 'Console API: one read surface over the VPS store and the Mac mini ledger'
status: To Do
assignee: []
created_date: '2026-09-23 16:23'
labels: []
dependencies: []
parent_task_id: TASK-283
priority: high
project: whatsapp
ordinal: 231000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked for this by name: "под это все надо спроектировать свою опишку" -- the console gets its own API rather than bending the board API it will be served next to.

The hard part is not the endpoints, it is that the data is split across two machines and the console
has to show them joined. A conversation lives on the VPS (data/wa.sqlite: wa_threads, wa_messages);
what actually happened on the handset lives on the mini (ledger.sqlite: phone_ops, outbound with its
ATTEMPTING/SENT/UNCONFIRMED/ABSENT/NOT_ATTEMPTED states, inbound, journal). A message row on the VPS
and the ledger row that typed it are joined by client_msg_id (app/wa/bridge_ids.py mints it
deterministically from phone, turn_key, action and bubble_index); a debug screenshot or recording is
joined to work by op_id.

Today there is no read API for the mini ledger at all. The executor exposes /v1 verbs that DRIVE the
phone, not a way to read its books; bridge/server.py has GET /v1/health and per-op status, nothing
more. Whatever this task builds must not become a second way to make the handset do things.

Ivan settled the placement on 2026-09-23: the API runs on the VPS (tasker-dispatcher-01), it goes to
the Mac mini for the handset-side data, and the frontend is served from the same domain as the board
frontend (https://pflege-board.exe.xyz). The VPS already reaches the mini two ways -- an ssh -L
tunnel to the executor HTTP port, and ssh/scp for staging media -- so the question for this task is
which of those the ledger read rides on, not whether a path exists.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The API is documented the way docs/api.md documents the board API, including every response shape
- [ ] #2 A conversation can be read joined: VPS message rows with the handset outcome of each one attached
- [ ] #3 The mini ledger is readable without opening any new way to drive the handset
- [ ] #4 Every endpoint is read-only except the task-scheduling one, and a test proves the read-only ones reject writes
- [ ] #5 The API answers with what it does not know rather than guessing when the mini is unreachable
<!-- AC:END -->
