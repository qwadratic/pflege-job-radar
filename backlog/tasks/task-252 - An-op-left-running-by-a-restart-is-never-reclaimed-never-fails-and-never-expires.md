---
id: TASK-252
title: >-
  An op left running by a restart is never reclaimed, never fails, and never
  expires
status: To Do
assignee: []
created_date: '2026-09-23 08:03'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 199000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Found independently by 2 review lenses. Location: bridge/ledger.py:547. Severity: operator-blind. 

HOW IT HAPPENS: A deploy, OOM or mini reboot lands while an op is mid-send. On restart the dispatcher walks past that row forever; the client polling it sees `running` until its own budget expires. Queued ops behind it DO drain -- including one enqueued minutes or hours earlier, whose conversational reply is stale by the time it is typed.

WHAT IT COSTS: A permanent leak of held artefacts and dead rows, and -- more to the point -- the one moment when a send's fate is genuinely unknown produces no signal anywhere, while the outbound row it left behind sits ATTEMPTING and unresolvable by anything but a manual reconcile.

PROPOSED DIRECTION (not a decision): A `running` row at startup is a crashed op by definition -- one process owns that table. Mark those rows failed on startup with an error naming the restart, which lets retention's existing logic take over (a send resolves off its own client_msg_id; the keyless kinds land in the manual resolve path TASK-230 already built). Put phone_ops state counts in /v1/health next to the dispatcher heartbeat. Queued ops surviving a restart also want a freshness check: a conversational reply queued long enough to be stale should be refused rather than typed.

VERIFICATION NOTES: CONFIRMED on every clause. claim_next_op selects `where state = ?` with OP_QUEUED only (ledger.py:546-548); nothing anywhere reads rows in `running` -- server.py::main starts the dispatcher (server.py:503-506) with no startup reclaim. sweep deletes phone_ops only `where finished_at is not null` (ledger.py:1103-1105), so the row is immortal. retention.classify_op_artifact returns `("hold", f"op still {row['state']}")` for anything that is neither OP_DONE nor OP_FAILED (retention.py:97), so its screenshots and recording are held forever. Executor.health (executor.py:801-828) reports ops_dispatcher.heartbeat() -- poll_interval, alive, debug_capture -- and ledger.queue_counts(), which is the OUTBOUND table, not phone_ops: there are no phone_ops state counts anywhere in the health body. And ledger.begin (ledger.py:444) has already written the outbound row, so a crash mid-send leaves it ATTEMPTING, which only a reconcile can resolve and nothing runs reconcile (finding 5).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->
