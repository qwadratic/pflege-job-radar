---
id: TASK-287
title: 'First-turn WhatsApp reply latency is ~5 minutes, dominated by cold VPS compose'
status: To Do
assignee: []
created_date: '2026-09-23 21:18'
updated_date: '2026-09-23 22:02'
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
- [ ] #1 The 2m43s cold-compose cost is broken down by actual profiling (CLI/session startup vs board_snapshot.json load vs model reasoning time), not guessed
- [ ] #2 Ivan has picked a direction from the proposed options before implementation starts
- [ ] #3 First-turn latency is measurably reduced without a quality regression in the reply (verified against the existing dialog-rules/quality test lanes plus a live check)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Profiling instrumentation added (Ivan chose 'profile first' over guessing): app/wa/luna_brain.py::_live_reply now reads the tools_ready stamp's 'at' timestamp before deleting it (previously generated and discarded every turn) and logs a single line per turn: fresh/resumed, total wall time, time-to-tools-ready (CLI cold-start + MCP handshake + board_snapshot priming), and the CLI's own duration_ms/duration_api_ms/num_turns from its JSON envelope (already computed by claude -p, previously thrown away). Logged at WARNING (not INFO) because this process sets up no root logging config -- matches the existing convention in router.py/refusal.py/bridge_api.py, confirmed INFO would be silently dropped. Verified firing via tests/test_wa_luna_brain.py --log-cli-level=WARNING. tests/test_wa_luna_brain.py -k live_reply: 10/10 pass. Full lane has 27 pre-existing failures (shortlist/market-snapshot tests) confirmed unrelated via git stash diffing -- present before this change too. No behavior change, read-only. Next step: deploy, then read journalctl -u pflege-wa.service on the next cold turn for a real phase breakdown before picking an optimization.
<!-- SECTION:NOTES:END -->
