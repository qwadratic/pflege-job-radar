---
id: TASK-259
title: >-
  Nothing on either machine ever polls /v1/health, so every subsystem's liveness
  signal has no consumer
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
ordinal: 206000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/server.py:5. Severity: operator-blind. 

HOW IT HAPPENS: InboundWatcher's thread ends (sqlite I/O error inside its own except handler, watcher.py:90). systemd still shows pflege-wa-bridge active because the HTTP server thread, the ops dispatcher and the relay are unaffected. /v1/health reports watcher.alive false and a frozen last_ok_at exactly as designed -- and nobody fetches it. Inbound is dead until a human hand-runs `wa_bridge health --json` or a candidate complains elsewhere.

WHAT IT COSTS: Every 'this is why the counter is in /v1/health' argument in the codebase (watcher.py:23-24, executor.py:107-118, server.py:5, INSTALL.md) terminates in a consumer that was never built. A deaf watcher, a dead dispatcher, a dead media/identity watcher and a stalled maintenance loop all look identical from outside: green `systemctl is-active` and silence. The backlog already admits it (task-132 audit 2026-09-22: 'verified NOT built beyond a docstring pointer'), but the code comments claim the opposite, so the next reader inherits false comfort.

PROPOSED DIRECTION (not a decision): Build the smallest possible poller -- a systemd timer on the mini (or on the VPS behind the existing ssh -L) that fetches /v1/health, evaluates a named handful of conditions (watcher.last_ok_at age, every `alive` flag, phone_ops depth, inbound oldest_unacked_at, maintenance age) and shouts where a human reads -- OR correct server.py:5 and watcher.py:24 so they stop asserting an alarm that does not exist. Choosing the alarm conditions is the actual work; the transport (journald + a WhatsApp message to Ivan's own number) is secondary.

VERIFICATION NOTES: Verified by exhaustive grep: the only non-test callers of /v1/health are relay_pull.Relay.health() (used ONLY by the hand-run `--probe` branch, relay_pull.py:360-368), app/wa/bridge.py::Client.health (used by tools/wa_bridge.py cmd_health, hand-run) and app/wa/bridge_api.py::wa_bridge_health (/api/wa/bridge-health -- which itself has zero callers anywhere in the repo, greps clean outside its own definition). deploy/ contains no timer for it (catchup, followups, purge-test, known-phones-export only). The relay's own loop calls /v1/outbox, never /v1/health. The thread-death half of the scenario is also reachable: InboundWatcher.cycle() catches around drain_inbound, but executor.clock() (watcher.py:83), ledger.note() inside the two except handlers (watcher.py:90, 97) and _check_idle_dirty's ledger.note (watcher.py:134) are unguarded, so a sqlite-level failure escapes cycle() and ends run() (watcher.py:140-144) while the HTTP thread stays up. Comments asserting the alarm exists are real: server.py:5, watcher.py:23-24. Not a compliance item.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->
