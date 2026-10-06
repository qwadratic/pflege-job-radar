---
id: TASK-313
title: >-
  P1: Dialog test bench — replay, record, judge, compare (base for every later
  harness change)
status: To Do
assignee: []
created_date: '2026-09-26 08:47'
labels:
  - bench
  - measurement
dependencies: []
priority: high
project: whatsapp
ordinal: 1
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every harness change so far was checked with ad-hoc scratch probes. Nothing is stored or compared, and nothing is judged against Ivan's rules. The llm-marked persona and e2e tests never run by default. Ivan, 2026-09-25: "тестинг должна быть система". Do this first; it makes every later harness task cheaper. Model choice and latency work run on the same bench.

**Existing pieces**
- tests/test_wa_luna_personas.py: scripted fictional personas through the real CLI, `-m llm`.
- tests/test_wa_luna_e2e_funnel.py: a model plays the candidate and improvises, through to a queue entry.
- app/wa/luna/shadow_run.py: the next reply on a copy of the prod DB, never sends.
- 2026-09-25 session scratch probes, not in the repo: run_probe.py scenarios s2/s3/close, and speed/t.py, a timing wrapper around Client.reply, the closing gate and turn.

**Baseline measured 2026-09-25** (S2 warming turn, Sonnet 5):

| Effort | Brain | Turn |
|---|---|---|
| max | 125 s | 138 s |
| high | 9 s | 24 s |
| medium | 9 s | 24 s |

- Medium re-introduced "Ich bin Valentina" mid-thread.
- The closing gate (Haiku) takes 9-12 s per call, mostly CLI spawn.
- Live since 2026-09-25 22:37 UTC: WA_LUNA_MODEL=claude-sonnet-5, WA_LUNA_EFFORT=high.

**Agreed direction**
- One runner over three sources:
  - scripted personas;
  - an improvising candidate;
  - real threads cut at turn N: what the brain says now vs what was sent then. Practice over theory.
- Every run stored: bubbles, per-stage timings, tokens, model/effort, git sha. Any two runs can be diffed.
- A judge in two parts:
  - code checks: bubble count, URL, question at the end, latency;
  - a model judge reading the same modular prompt blocks the brain uses: IDEOLOGY, the last-bubble hook, the yellow flag.
- A phone-style report: the notification preview (last bubble) on top, the chat below, readable on mobile. A bubble Ivan marks bad becomes a new scenario.
- A smoke set (~5 scenarios) before every deploy; the full set before model or prompt changes.

**Model bake-off** (was TASK-308)
- Candidates: Sonnet at several efforts, Haiku at max, Opus 5.5 at several efforts.
- Run it for the brain, the closing gate and the status evaluator (P4), weighing price, quality and latency.
- The result feeds one coarse env knob: low / medium / high.

**Latency** (was TASK-309)
- Measure end to end, from inbound capture to send.
- Ideas so far: a warm CLI process for the gate; the gate running concurrently with post-processing.

**Yes/no recovery check** (was TASK-305): see its ACs below.

Folded here: TASK-312, TASK-308, TASK-309, TASK-305. Their full text is kept in the archive.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A scenario set in the repo replays offline against the brain and never touches the rail or data/wa.sqlite
- [ ] #2 Sources: scripted personas, an improvising candidate, and real threads cut at turn N (no candidate content copied out of the DB)
- [ ] #3 Each run stores bubbles, per-stage timings, tokens, model/effort and git sha; any two runs can be diffed
- [ ] #4 The judge combines deterministic checks with a model judge that reads the brain's own prompt blocks
- [ ] #5 A phone-style report (notification preview on top) is readable on mobile, and a bubble marked bad becomes a scenario
- [ ] #6 A smoke set runs before every deploy
- [ ] #7 Bake-off: the brain, the closing gate and the status evaluator each get a recommended model/effort with evidence, and one env knob low/medium/high maps to them
- [ ] #8 End-to-end latency is measured from inbound capture to send, per stage, and each speed idea is measured before and after
- [ ] #9 [TASK-305] Aggregate-count check run against real candidate replies to two-button (yes/no) offers on wa_messages history, no candidate content copied anywhere
- [ ] #10 [TASK-305] Report states how often the keyword tier matched, mismatched and fell through to Luna as free text
- [ ] #11 [TASK-305] If the match rate is below what TASK-224's own 90pct-or-escalate rule required, the task stops and escalates rather than adding regex; the keyword tier is adjusted only on a real, observed failure mode
<!-- AC:END -->
