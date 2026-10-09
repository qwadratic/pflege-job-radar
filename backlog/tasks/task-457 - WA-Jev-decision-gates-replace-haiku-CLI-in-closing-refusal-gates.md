---
id: TASK-457
title: 'WA: Jev decision gates replace haiku CLI in closing + refusal gates'
status: To Do
assignee: []
created_date: '2026-10-09 19:12'
labels:
  - wa
  - llm
dependencies: []
priority: high
ordinal: 339000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-09. Jev (TypeSafe AI via OpenRouter /api/alpha/decisions) takes over the two per-turn DECISION gates from the haiku claude-CLI subprocesses: closing_gate (EVERY reply, serial on critical path, 30s timeout) and refusal (decline branch). Haiku keeps text generation only (expose_shrink). New app/wa/luna/jev.py: stateless requests-based client (requests already in requirements.txt), WA_OPENROUTER_API_KEY/WA_JEV_MODEL/WA_JEV_TIMEOUT_SEC in config, decide() raises on anything unusable -> gates keep existing failure directions (closes=True unchecked / is_refusal=False keep talking). Gates keep transport injection + Verdict contract byte-identical so all labeled unit tests pass unchanged. Proof: labeled tests + live before/after bench on latency + verdict agreement.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 jev.py decide() unit-tested with fake transport: payload shape, HTTP failure, missing/non-numeric decision all raise RuntimeError
- [ ] #2 closing_gate + refusal _live_transport swapped to Jev; Verdict contract + failure directions unchanged; all existing labeled gate tests pass unmodified
- [ ] #3 config: WA_OPENROUTER_API_KEY (empty = loud at call, never silent default), WA_JEV_MODEL default typesafe/jev-1.13, WA_JEV_TIMEOUT_SEC default 5s
- [ ] #4 live before/after bench: Jev vs haiku CLI latency + verdict agreement on labeled closing + refusal cases
- [ ] #5 .env.example documents the new placeholders
<!-- AC:END -->
