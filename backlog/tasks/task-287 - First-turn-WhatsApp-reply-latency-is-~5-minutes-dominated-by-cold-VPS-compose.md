---
id: TASK-287
title: 'First-turn WhatsApp reply latency is ~5 minutes, dominated by cold VPS compose'
status: Done
assignee: []
created_date: '2026-09-23 21:18'
updated_date: '2026-09-24 16:28'
labels:
  - whatsapp
  - performance
dependencies: []
ordinal: 240000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live UAT, 2026-09-23: Ivan flagged the reply speed as a real concern (wants a fast back-and-forth, it is what keeps a candidate engaged). Measured by cross-referencing journalctl -u pflege-wa.service webhook-accept timestamps against the mini's outbound table timestamps: turn 1 (cold Luna session) = 2m43s VPS compose + ~1m43s phone-side typing/tick simulation = 4m47s total; turn 2 (warm session, same thread) = 50s compose + ~1m44s phone side = ~2m34s total. The phone-side leg is fixed governor/typing-simulation time and stable across both turns; the variable, dominant cost is VPS-side compose on a cold session. Candidates: cold Claude Code CLI/session startup, LUNA_EFFORT=max reasoning cost (app/wa/config.py), and/or the ~7MB board_snapshot.json being read fresh on a cold spawn. Not yet profiled which of these actually dominates the 2m43s -- this task is to investigate and implement, not just measure.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The 2m43s cold-compose cost is broken down by actual profiling (CLI/session startup vs board_snapshot.json load vs model reasoning time), not guessed
- [x] #2 Ivan has picked a direction from the proposed options before implementation starts
- [ ] #3 First-turn latency is measurably reduced without a quality regression in the reply (verified against the existing dialog-rules/quality test lanes plus a live check)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Profiling instrumentation added (Ivan chose 'profile first' over guessing): app/wa/luna_brain.py::_live_reply now reads the tools_ready stamp's 'at' timestamp before deleting it (previously generated and discarded every turn) and logs a single line per turn: fresh/resumed, total wall time, time-to-tools-ready (CLI cold-start + MCP handshake + board_snapshot priming), and the CLI's own duration_ms/duration_api_ms/num_turns from its JSON envelope (already computed by claude -p, previously thrown away). Logged at WARNING (not INFO) because this process sets up no root logging config -- matches the existing convention in router.py/refusal.py/bridge_api.py, confirmed INFO would be silently dropped. Verified firing via tests/test_wa_luna_brain.py --log-cli-level=WARNING. tests/test_wa_luna_brain.py -k live_reply: 10/10 pass. Full lane has 27 pre-existing failures (shortlist/market-snapshot tests) confirmed unrelated via git stash diffing -- present before this change too. No behavior change, read-only. Next step: deploy, then read journalctl -u pflege-wa.service on the next cold turn for a real phase breakdown before picking an optimization.

Deployed 2026-09-23 ~23:15 UTC as part of commit 4fa1b47, pflege-wa restarted and verified healthy. Awaiting the next cold turn on the live rail to read journalctl -u pflege-wa.service for the actual fresh=True luna_turn_timing line.

Profiling data (7 cold turns, journalctl -u pflege-wa.service since deploy 2026-09-23 23:15): tools_ready_ms (CLI cold-start + MCP handshake + board_snapshot priming) is stable at ~1.9s across every turn -- NOT the bottleneck, ruling out the 'board_snapshot.json load' and 'cold CLI startup' hypotheses. cli_duration_ms (~= api_duration_ms) is 72-96% of total wall time and scales with num_turns (tool round-trips): 2 turns=7.1s, 9 turns=73.0s, 10 turns=46.4s. The dominant, variable cost is inside the claude -p call itself, driven by how many tool round-trips a turn needs, not session/snapshot overhead.

SURPRISE finding while implementing Ivan's pick: WA_LUNA_EFFORT=high was already set in the live .env (mtime 2026-09-23 07:34 UTC, predating the whole TASK-287 incident and every profiled turn) -- the code's own default said 'max' (TASK-229) but the live service had never actually run at max; it ran at 'high' for the whole profiling window above. Ivan's decision (2026-09-24, verbal, relayed via a partner-feedback message): stay at 'high', do not pursue the num_turns-reduction direction for now ('оставляем как есть'). Updated app/wa/config.py's LUNA_EFFORT default from 'max' to 'high' so the code stops claiming a tier the live env never ran at -- no behavior change (the .env override already won), verified no test references the old default. No fresh restart needed for this specific change since runtime behavior is unchanged; pflege-wa.service already running with WA_LUNA_EFFORT=high (confirmed via .env) since before today.

AC#3 not checked: there is no before/after to measure a 'reduction' against, since the live service never actually ran at 'max' during this investigation -- the .env override meant 'high' was already the operative value. If Ivan wants the num_turns-reduction direction pursued later, that is new, separate work.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Profiled 7 real cold turns: session/snapshot startup is a stable ~1.9s, not the bottleneck -- 72-96% of wall time is inside the claude -p call itself, scaling with tool round-trips (num_turns). Ivan picked 'stay at high, don't chase num_turns reduction for now.' Turned out WA_LUNA_EFFORT=high was already the live setting (the code's 'max' default was aspirational, never actually deployed) -- corrected the code default to match, no runtime behavior change. AC#3 (measured reduction) not applicable: nothing changed at runtime to measure a before/after against.
<!-- SECTION:FINAL_SUMMARY:END -->
