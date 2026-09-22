---
id: TASK-132
title: Bridge health timer with a conjunction alert and a host free-space check
status: To Do
assignee: []
created_date: '2026-09-21 01:23'
updated_date: '2026-09-22 06:10'
labels:
  - wa-transport
dependencies:
  - TASK-140
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: medium
type: feature
ordinal: 140000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M4/M11, rescoped by decision-8 (2026-09-21). Alerting on "bridge offline" is still the wrong condition and would page constantly; the conjunction stays. What changed is which host the disk alarm watches, and what else the poll has to see now that we share a handset with somebody else lane.

THE FREE-SPACE ALARM MOVES HOSTS. The remote machine has 457 G at 4 percent used -- it is not the cliff. OUR VPS is: 38 G total, 1.1 G free, 98 percent. That is the nearest operational cliff on either machine and a full disk takes pflege-wa down regardless of transport. Watch ours, not theirs.

FOUR SIGNALS ADDED, all of them things that can fail silently under a wrap architecture:
1. Tunnel restart count. The link is measured at 11.4 percent retransmit with cwnd pinned at 3, Ethernet DOWN and everything on Wi-Fi. A tunnel that flaps every few minutes is not the same as one that is up.
2. Their daemon liveness. If his lane dies, ours keeps taking the flock and nothing looks wrong from our side -- but the handset is then a single-master device without anyone noticing the change.
3. Flock contention count. Already observed once as "device error: phone lock busy". Rising contention is the early signal that our one-bubble-per-acquisition protocol is not being honoured on one side or the other.
4. Dependency-commit drift on their three driver modules (TASK-140 records it; this timer alarms on it).

The conjunction itself is unchanged: session not usable, OR the inbound cursor is stale, OR (something is queued AND the oldest queued item is old AND we are inside the send window). A dead handset during the send window burns the campaign schedule silently while claimed_at-based pacing keeps claiming.

UNRESOLVED INPUT, needs Ivan before this ships: what the pager actually is. This repo deliberately has no alert channel (app/wa/config.py and api.py both say so), and its only real signal today is stuck_reply plus last_send_error on GET /wa/threads. Either a real channel is wired with Ivan consent, or the honest statement is that Ivan asleep means nothing is delivered until morning. Write down which; do not invent a channel.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The check alerts only on the documented conjunction and is silent on an ordinary healthy poll
- [ ] #2 Free disk is watched on OUR VPS root filesystem, not on the remote machine, and the threshold and current headroom are stated
- [ ] #3 Tunnel restart count over a window is served in health and alarms above a stated rate
- [ ] #4 The colleague daemon liveness is served in health, and its transition from alive to dead raises an alarm distinct from our own rail being down
- [ ] #5 Flock contention count is served in health and alarms on a rising rate
- [ ] #6 Drift on their three driver modules raises an alarm naming which file changed
- [ ] #7 The alert delivery channel is written down explicitly, including the answer that there is none and stuck_reply is the first signal Ivan sees
- [ ] #8 A test drives each disjunct of the conjunction independently and asserts no alert when only part of a conjunct is true
- [ ] #9 deploy/ carries the timer as a template only; nothing is installed, enabled or started by this task
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified NOT built beyond a docstring pointer. bridge/server.py:5 names GET /v1/health as 'what the 3-minute timer alarms on (TASK-132)' but no conjunction-alert logic, no VPS free-space check, no tunnel-restart counter, no colleague-daemon-liveness signal and no flock-contention counter were found anywhere in app/, bridge/ or deploy/ -- grepped for each by name, only this one comment hit. Status and description remain accurate as written.
<!-- SECTION:NOTES:END -->
