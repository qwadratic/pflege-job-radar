---
id: TASK-148
title: >-
  One-step CLI for the handset operations, with the client half and
  deterministic keys
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 13:23'
updated_date: '2026-09-22 06:08'
labels:
  - wa-transport
dependencies:
  - TASK-147
documentation:
  - docs/whatsapp.md
priority: high
type: feature
ordinal: 156000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-147 put the operations on the mini behind loopback HTTP with a bearer token. That is not yet what Ivan asked for on 2026-09-21: he asked for operations a model or an operator calls in ONE step, from where they actually sit, which is the VPS. This is the other half of the same promise -- the client methods for the new routes and a single command surface over the ssh -L the harness already keeps open. It is also where re-run safety has to be decided, because the caller mints the key: a command re-typed after a dropped connection must replay, not send a second message to a real person. Lane: tools/wa_bridge.py, app/wa/bridge.py (client methods only), tests/test_wa_bridge_cli.py. No pacing, no caps and no identity rules live here -- they are the executor's, and a second copy of them would be a second authority.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 tools/wa_bridge.py exposes health, chats, read, send, broadcast, clear-chat and delete-chat, each one call into app/wa/bridge.Client and no phone logic of its own
- [x] #2 send and broadcast mint deterministic client_msg_ids (app/wa/bridge_ids), so re-running the identical command replays and sends nothing; sending the same text again on purpose needs --key or --attempt
- [x] #3 broadcast plans and prints without --send; a recipient file (.csv with a phone header, .json list of objects) is read whole or refused naming the failing line, and a repeated recipient is refused
- [x] #4 both sending commands refuse without WA_AUTOSEND=1, introduce no cap of their own, and --pacing can only ask the governor for a slower gap
- [x] #5 clear-chat and delete-chat without --confirm print the row and the visible message count and destroy nothing; with --confirm they hand the row identity to the executor and print its verification and audit id
- [x] #6 a chat the list does not show, or a title the handset ties to two numbers, is an error naming what was found, never a guess and never a no-op
- [x] #7 exit codes separate done / needs attention / usage or configuration error with nothing attempted / not finished / interrupted
- [x] #8 offline tests drive every subcommand against a fake client including the refusal paths, and the offline suite stays green
- [ ] #9 the usage text names the two env files needed on tasker-dispatcher-01: .env for WA_AUTOSEND and ~/.local/state/pflege-wa-bridge/rail.env for WA_BRIDGE_URL and WA_BRIDGE_TOKEN (it currently says .env alone, which is wrong on this host)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Landed 2026-09-21: tools/wa_bridge.py (7 subcommands, exit codes 0/1/2/3/130), client methods list_chats, read_thread, broadcast, broadcast_keys, broadcast_status, broadcast_stop, broadcast_runs, clear_chat, delete_chat in app/wa/bridge.py, tests in tests/test_wa_bridge_cli.py. Remaining: AC#9 (usage text names rail.env), and a live one-step proof of each read-only command on the handset.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified against the tree. tools/wa_bridge.py (672 lines) exposes health/chats/read/send/broadcast/clear-chat/delete-chat as thin calls into app/wa/bridge.Client; deterministic keys via app/wa/bridge_ids (send --key defaults to a digest of the body, so a re-run replays); WA_AUTOSEND gate on both sending commands (tools/wa_bridge.py:326,371); destructive commands print-then-refuse without --confirm. tests/test_wa_bridge_cli.py: 57 tests, all passing (confirmed 2026-09-22), covering every subcommand plus the refusal paths (ambiguous chat, repeated recipient, malformed pacing, lost-answer destruction reporting, exit-code separation). Full offline suite green: 2312 passed. AC#9 CONFIRMED STILL OPEN: tools/wa_bridge.py:17 usage text still reads 'Load .env first ... WA_BRIDGE_URL, WA_BRIDGE_TOKEN, WA_AUTOSEND' -- it does not name ~/.local/state/pflege-wa-bridge/rail.env, which is where WA_BRIDGE_URL/WA_BRIDGE_TOKEN actually live on tasker-dispatcher-01 per deploy/wa-bridge/INSTALL.md. This is a one-line doc-string fix in tools/wa_bridge.py, outside this audit's lane (backlog/** only) to make. Left In Progress rather than Done: 8/9 ACs are solid, but this is a real, specific, named gap with nobody currently assigned to close it -- not a stale status.
<!-- SECTION:NOTES:END -->
