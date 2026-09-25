---
id: TASK-303
title: >-
  Russian operator commands: a cron-started worker that decodes them and hands
  them to the running session
status: To Do
assignee: []
created_date: '2026-09-25 00:02'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 256000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24/25, his own crystallised flow: a Sonnet session on medium effort, started by cron every 5 minutes inside a 09:00-22:00 window, responsible for exactly one backlog card. It decodes Russian feedback from Ivan or Valentyn arriving on the WhatsApp test threads, fills that card with the decoded request plus the relevant database context, and then messages the long-running session that does the work. Delivery of Russian messages into the queue is already solved by app/wa/luna/agent_note_gate.py; this task is only the worker and the hand-off.

The hop was verified empirically on 2026-09-25 rather than assumed -- six independent probes, all delivered. What they established, and what this task must respect:
- A headless 'claude -p' session CAN see the running session via ListAgents and SendMessage to it; the reverse direction also works, and an incoming message wakes an idle session rather than waiting for its next prompt.
- The registry is ~/.claude/daemon/roster.json under a supervising 'claude daemon run' process. HOME is load-bearing: with HOME unset the invocation hangs and has to be killed.
- '--permission-mode bypassPermissions' is refused before the model even starts, by this host's auto mode classifier ('Create Unsafe Agents'). Plain default mode plus an explicit --allowedTools list works.
- 'claude --bg' and '-p' are mutually exclusive; 'claude agents' needs --json without a TTY; 'claude logs' emits raw ANSI and is useless to a script -- read ~/.claude/jobs/<id>/state.json instead.
- A session has three different identifiers (ListAgents ref, CLI background id, session UUID) and only the ListAgents ref addresses it. The target session's display name is derived from its title and changes on restart, so it cannot be hard-coded.
- One idle Opus run cost 0.39 USD measured. At 5-minute ticks across a 13-hour window that is roughly 60 USD a day spent discovering nothing.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A shell-level check runs before the model starts and exits without spending a token when no operator note is pending
- [ ] #2 The worker runs on Sonnet, not the session default
- [ ] #3 cron invokes the versioned binary path, not the /home/claude/.local/bin/claude symlink, so an update cannot silently change what runs
- [ ] #4 HOME is set explicitly in the cron entry, and no bypass or skip-permissions flag is used; the tools it needs are named in --allowedTools
- [ ] #5 The worker resolves its target by calling ListAgents and matching a pattern at send time, never a hard-coded session name or a CLI-returned id
- [ ] #6 The window is 09:00 to 22:00 and the tick is 5 minutes
- [ ] #7 The worker writes the decoded request and the relevant database context onto its backlog card before it messages anyone, so the work survives the message being missed
- [ ] #8 A run where the hand-off fails leaves the card filled and says so loudly, rather than dropping the request
<!-- AC:END -->
