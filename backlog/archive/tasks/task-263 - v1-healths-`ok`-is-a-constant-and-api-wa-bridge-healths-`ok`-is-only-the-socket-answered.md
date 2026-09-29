---
id: TASK-263
title: >-
  /v1/health's `ok` is a constant, and /api/wa/bridge-health's `ok` is only 'the
  socket answered'
status: To Do
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-26 08:49'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 210000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/executor.py:766. Severity: operator-blind. 

HOW IT HAPPENS: After the dispatcher thread dies, a human (or the first version of the health timer) hits /api/wa/bridge-health, sees ok: true, and concludes the rail is fine while nothing has been typed for hours.

WHAT IT COSTS: The field any future monitor, deploy check or human reads first is the one field that can never be false, which converts a partial outage into a green light and makes the eventual health timer easy to build wrong.

PROPOSED DIRECTION (not a decision): Compute the top-level verdict from the parts rather than asserting it: ok false (or a named `degraded` list) when a watcher is not alive, when last_ok_at is older than a few intervals, when the dispatcher is not draining, when maintenance has not run within its period, or when the inbound backlog's oldest unacked item is older than a stated age. Those thresholds are the same decision the missing alarm needs -- an argument for doing both at once.

VERIFICATION NOTES: executor.health returns {'ok': True, ...} unconditionally (executor.py:766) with every real state nested below. bridge_api.wa_bridge_health returns {'ok': status == 200, 'status', 'health': body} (bridge_api.py:94) -- correction to the finder: it ignores the executor's own `ok` entirely and keys on the HTTP status, which is the same failure in a different place. With the watcher dead, the dispatcher gone, maintenance stopped and 400 ops queued, both endpoints answer ok: true. Impact today is latent: /api/wa/bridge-health has no caller anywhere in the repo, so this is currently a trap for the alarm finding 1 asks to build rather than an active blind spot.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26: folded into TASK-315 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
