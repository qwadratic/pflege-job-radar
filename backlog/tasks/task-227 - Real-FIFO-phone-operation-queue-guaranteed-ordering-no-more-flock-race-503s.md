---
id: TASK-227
title: 'Real FIFO phone-operation queue: guaranteed ordering, no more flock-race 503s'
status: To Do
assignee: []
created_date: '2026-09-23 03:11'
labels: []
dependencies: []
project: whatsapp
ordinal: 174000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-225 fix, part B. bridge/server.py is a ThreadingHTTPServer with no serialization beyond a plain flock (bridge/adb_driver.py PhoneLock, no FIFO guarantee, 30s timeout then 503 device_unavailable). Replace with a durable phone_ops table (mirrors broadcast_item's position+status+index shape, bridge/ledger.py:56-61,144-160) and one dispatcher thread that claims the oldest queued row and generically dispatches getattr(executor, kind)(**args). HTTP routes that touch the phone change to enqueue+answer immediately with {op_id, state:queued} (Ivan's explicit choice); new GET /v1/ops/<op_id> mirrors GET /v1/broadcasts/<id>. app/wa/bridge.py::Client absorbs enqueue+poll-until-terminal internally so every existing caller (luna_brain, campaign.py, tools_server.py, catchup.py, followups.py, tools/wa_bridge.py) keeps its current synchronous, confirmed-result contract unchanged -- this preserves bridge/server.py's own documented 'never lie about unconfirmed work' principle (see its module docstring on why there is deliberately no bare 202 today).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Two operations enqueued concurrently execute in strict FIFO order, never both racing the flock
- [ ] #2 GET /v1/ops/<op_id> reports queued/running/done/failed with result or error, mirroring the broadcast status route's shape
- [ ] #3 Every existing caller of app/wa/bridge.py::Client keeps working with zero code changes -- the client's public methods still return a confirmed result or raise, never a bare queued state
- [ ] #4 The dispatcher runs the pre-flight dirty check from the sibling task before each job
<!-- AC:END -->
