---
id: TASK-303
title: >-
  Russian operator commands: a cron-started worker that decodes them and hands
  them to the running session
status: In Progress
assignee: []
created_date: '2026-09-25 00:02'
updated_date: '2026-10-06 12:49'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 256000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24/25, his own crystallised flow: a Sonnet session on medium effort, started by cron every 5 minutes inside a 09:00-22:00 window, responsible for exactly one backlog card. It decodes Russian feedback from Ivan or the parallel operator arriving on the WhatsApp test threads, fills that card with the decoded request plus the relevant database context, and then messages the long-running session that does the work. Delivery of Russian messages into the queue is already solved by app/wa/luna/agent_note_gate.py; this task is only the worker and the hand-off.

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
- [ ] #1 A shell gate runs before any model: outside 09:00-22:00 Europe/Vienna (the box clock is UTC), or when wa_agent_notes holds nothing a worker could take, the tick exits without starting claude
- [ ] #2 The wrapper calls the /home/claude/.local/bin/claude symlink so CLI updates are picked up (Ivan, 2026-09-25), and refuses to start when the symlink does not resolve to a non-empty executable, recording why where the session will see it
- [ ] #3 Decoding runs on Sonnet at medium effort with no tools at all; the hand-off runs on Sonnet with only ListAgents and SendMessage
- [ ] #4 The target is the exact session name wa-harness (pinned with claude -n, overridable by WA_AGENT_NOTE_TARGET), resolved through ListAgents at send time; zero or several matches is a failed hand-off, never a guess
- [ ] #5 One note per tick: the worker claims it, writes one backlog card (English, verbatim Russian quoted, decoded request, relevant database context) and records the card id on the note before messaging anyone; a retry never creates a second card for the same note
- [ ] #6 The hand-off message is composed by code and carries no operator-derived text: only the note id, the card id and the commands to read and close it
- [ ] #7 A failed hand-off leaves the card filled, returns the note to pending for the next tick, and says so on the note's progress trail and in the worker health file; after 5 attempts the note is closed as blocked and the operator gets the Russian completion note saying so
- [ ] #8 A note whose completion message failed to send is retried by the worker until it is delivered
- [ ] #9 The UserPromptSubmit hook reads wa_agent_notes and the worker health file instead of TASK-297, is wired where the wa-harness session actually loads it, stays silent when nothing is open, and never breaks a turn
- [ ] #10 Offline tests cover every state transition with a faked claude and a faked backlog; a live end-to-end run with a synthetic note reaches the wa-harness session
- [ ] #11 HOME and PATH are set explicitly; no bypass or skip-permissions flag is used; every claude -p call names its tools explicitly and runs with --strict-mcp-config and --no-session-persistence. No dollar cap (Ivan, 2026-09-25: 'лимит бюджета снять'; the CLI has no token cap to use instead), and each call's token usage is written to the note's progress trail
- [ ] #12 Operator notes survive the nightly test-history purge, note ids are never reused (AUTOINCREMENT), and every outbound turn key of a note (ack, completion) is unique to that note forever, so the bridge ledger can never replay an older note's message in its place
- [ ] #13 Undelivered completion messages are retried in every tick independently of new notes: one completion that cannot be delivered never keeps a newer note from being decoded and handed off
- [ ] #14 A worker that exits non-zero without writing health leaves health.json ok:false for that tick, an orphaned in_progress note is reported rather than hidden, and no environment-file value ever reaches the log
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Decided 2026-09-25 (finish-line rule, facts measured this morning):
- Newest CLI, not a pinned version: Ivan asked for it. The guard exists because the first update attempt left a 0-byte 2.1.282 behind.
- Exact name, not a pattern: claude -n pins the name other sessions see (verified on 2.1.282: -n wa-harness-probe showed up verbatim and SendMessage to it succeeded). --remote-control [name] does NOT set it; it only feeds the RC link slug. The old 2.1.270 process of this same session still lists as 'Pflege Hire: WA Harness', so a pattern would be ambiguous.
- Headless ListAgents on 2.1.282 with --strict-mcp-config --tools 'ListAgents SendMessage' sees wa-harness and costs 0.024 USD a call (0.11 with the default tool set).
- Window gated inside the wrapper with TZ=Europe/Vienna, because cron flavours differ on CRON_TZ and the box runs UTC.
- Hand-off text carries no operator-derived text, so nothing in a note can steer where the message goes.
- The environment files are parsed the way systemd's EnvironmentFile= parses them, not sourced: .env line 37 is not valid shell.
Pieces: app/wa/store.py (task_id, handed_off_at, state handed_off, release), app/wa/luna/agent_note_worker.py, tools/agent_note_cron.sh, tools/operator_queue_hook.py rewritten, agent_notes CLI loads the env itself, tests, docs runbook. Deploy: crontab line, hook wired in /home/claude/repo/.claude/settings.json, live E2E with a synthetic note.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-25 review round 1 rejected the first build (3/3). Ivan's decisions the same morning: A stop purging wa_agent_notes + non-reusing ids; B split completion retries from new notes; C crash visibility + no secrets in logs; D hook wiring at deploy; attempts cap 5 kept; dollar cap removed; log truncation dropped (CLAUDE.md no-safety-nets default, growth is negligible).
<!-- SECTION:NOTES:END -->
