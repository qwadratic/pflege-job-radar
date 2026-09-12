---
id: TASK-68
title: End-to-end two-agent synthetic funnel test suite and report
status: To Do
assignee: []
created_date: '2026-09-12 16:01'
labels: []
dependencies:
  - TASK-66
ordinal: 68000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked for the test suite to be extended end-to-end: synthetic personas driven through the whole funnel to a consenting close, matched against real clinics, producing a mailing-list-style output (candidates and at least one clinic email, ideally several clinics/buckets), with a report confirming it. He also asked to prove non-determinism on both sides of the exchange, not just the answerer -- the existing persona tests (tests/test_wa_luna_personas.py) use a fixed candidate script by design, so this new suite drives the candidate side with a live LLM persona too. See /home/claude/.claude/plans/wise-enchanting-crayon.md section 6.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New tests/test_wa_luna_e2e_funnel.py (llm-marked) has a _CandidateAgent using the same CLI-subprocess Client pattern with a persona system prompt, so both sides of each conversation are live model calls
- [ ] #2 3-4 persona archetypes run end-to-end from an opener through anonymous_send_consent, capped at a generous max-turn bound that is logged (not silently treated as success) if a persona does not converge
- [ ] #3 Each persona reaching consent is fed through TASK-66 build_queue_entry against a small fixture board with at least one resolvable clinic_contacts row
- [ ] #4 Final assertions: at least 3 distinct candidates reach consent and a queue entry; the mailing-list view has at least one clinic with a resolved contact email
- [ ] #5 The test run produces a readable report (printed and captured, or written to a gitignored tests/.artifacts/ file) of each persona outcome, matched clinics, and contact-resolution status
- [ ] #6 Full offline suite stays green; the llm-marked suite is run explicitly and its report is shared back, not just pass/fail
<!-- AC:END -->
