---
id: TASK-60
title: >-
  WhatsApp brain: swap the LLM behind the same persona/rules/gates from OpenAI
  to Claude
status: Done
assignee:
  - '@claude'
created_date: '2026-09-12 12:53'
updated_date: '2026-09-12 12:55'
labels:
  - whatsapp
  - luna
  - claude
dependencies: []
references:
  - app/wa/luna/VENDORED.md
  - TASK-59
ordinal: 60000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The inbound WhatsApp harness (TASK-59) ships a deterministic, no-LLM brain. Separately, the production WhatsApp bot on tasker-dispatcher-01 (persona 'Valentina', internally 'Luna') runs the same kind of conversation on OpenAI, with an owner-locked constitution, a qualification gate, region scope and a live-market matching style. This task adds a second, swappable brain to the same harness that keeps that persona, those rules and those gates, but calls Claude instead -- so the harness can run either a deterministic ladder or an LLM-driven conversation, selected by config, without duplicating the transport. The source prompts are private/company-branded; what lands here is a rewrite that keeps the same structure and gates with company-specific wording genericized (see app/wa/luna/VENDORED.md for exactly what changed).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A second brain (WA_BRAIN=luna) answers a turn using the same persona, qualification gate, region scope and live-market-matching rules as the reference implementation, adapted and documented in app/wa/luna/VENDORED.md
- [x] #2 The qualification-reject and out-of-scope-region replies are locked, code-enforced text, never left to the model's own phrasing
- [x] #3 Opt-out (STOP) is handled before the model is ever called
- [x] #4 The model call goes through the already-authenticated claude CLI (non-interactive print mode), not a separate ANTHROPIC_API_KEY, and fails loudly (never silently) on a missing binary, timeout, non-JSON output, or a reply missing required fields
- [x] #5 The market data the model is given comes from this repo's own board filters (app/data.py), the same ones GET /api/jobs uses, not a separate remote call
- [x] #6 Tests run offline with a fake model reply; the default WA_BRAIN=deterministic behaviour and its existing tests are unchanged
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read the production Luna/Valentina prompt files on tasker-dispatcher-01 (candidate_luna_first.py: GOAL/RULES/THINK_ORDER/output contract; the constitution and qualification-knowledge JSON; candidate_locked_phrases.py) to port the actual gates verbatim in structure.
2. Since pflege-job-radar is public and the source is private/company-branded, rewrite (not copy) the constitution/rules into app/wa/luna/, genericizing company name and dropping infrastructure this repo does not have (document OCR, interview scheduling, clinic-submission email, manager CRM, proactive/quiet-hours) -- documented plainly in VENDORED.md. Qualification-knowledge JSON is generic regulatory content and is kept as-is.
3. app/wa/luna_brain.py: market_snapshot()/requirement_scoreboard() from app/data.py + app/wa/brain.py (no remote call); Client wraps the claude CLI (-p --restricted --output-format json --system-prompt ..., user payload over stdin), swappable via reply= for tests; three gates enforced in code (stop, qualification-reject, out-of-scope-region) so the model cannot rephrase them.
4. Wire WA_BRAIN=deterministic|luna into app/wa/config.py and app/wa/api.py, default deterministic so TASK-59's behavior and tests are untouched.
5. tests/test_wa_luna_brain.py offline: fake reply for the turn logic, mocked subprocess.run for the CLI call boundary, one webhook-level integration test with WA_BRAIN=luna.
6. Verify end-to-end against the real claude CLI (three manual runs: normal turn, qualification reject, out-of-scope region) before calling it done.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified end-to-end against the real claude CLI (three manual runs: normal turn asking the qualification question, qualification-reject gate firing the locked text, out-of-scope-region gate firing without a model call). Offline suite: tests/test_wa_luna_brain.py 28 passed; full repo suite 860 passed / 122 skipped / 6 failed, same 6 pre-existing failures as before this change (4 need Supabase credentials this sandbox lacks, 2 in test_career_crawl_section/test_ontology, both predating this work).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added app/wa/luna_brain.py + app/wa/luna/ (WA_BRAIN=luna): a second, swappable brain for the same harness that keeps the production bot's persona, qualification gate, region scope and live-market-matching rules -- rewritten and genericized for a public repo (app/wa/luna/VENDORED.md documents exactly what changed and why), calling Claude through the already-authenticated claude CLI instead of a separate API key. Three gates (opt-out, qualification-reject, out-of-scope-region) stay code-enforced with locked wording; everything else is the model's call from state the harness supplies (thread, card, market snapshot, requirement scoreboard) from this repo's own board data. Default WA_BRAIN=deterministic and TASK-59's 40 tests are unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
