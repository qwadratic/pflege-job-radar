---
id: TASK-68
title: End-to-end two-agent synthetic funnel test suite and report
status: Done
assignee:
  - '@claude'
created_date: '2026-09-12 16:01'
updated_date: '2026-09-12 17:24'
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
- [x] #1 New tests/test_wa_luna_e2e_funnel.py (llm-marked) has a _CandidateAgent using the same CLI-subprocess Client pattern with a persona system prompt, so both sides of each conversation are live model calls
- [x] #2 3-4 persona archetypes run end-to-end from an opener through anonymous_send_consent, capped at a generous max-turn bound that is logged (not silently treated as success) if a persona does not converge
- [x] #3 Each persona reaching consent is fed through TASK-66 build_queue_entry against a small fixture board with at least one resolvable clinic_contacts row
- [x] #4 Final assertions: at least 3 distinct candidates reach consent and a queue entry; the mailing-list view has at least one clinic with a resolved contact email
- [x] #5 The test run produces a readable report (printed and captured, or written to a gitignored tests/.artifacts/ file) of each persona outcome, matched clinics, and contact-resolution status
- [x] #6 Full offline suite stays green; the llm-marked suite is run explicitly and its report is shared back, not just pass/fail
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New tests/test_wa_luna_e2e_funnel.py (llm-marked): a _CandidateAgent (same claude -p CLI-subprocess pattern as luna_brain.Client, but free-text reply, own session dir) driven by a short per-persona system prompt (invented background, cooperative, replies naturally in German, agrees to reasonable asks so the funnel actually converges).
2. Loop candidate<->Luna turns (LB.turn) until anonymous_send_consent is true or a logged MAX_TURNS bound is hit.
3. 3 persona archetypes (Urkunde-qualified, Defizitbescheid path, Kenntnispruefung-passed-Urkunde-pending), each against a small fixture board (2-3 clinics, one clinic_contacts row seeded via TASK-64's contacts.save_contact so at least one contact resolves).
4. Each persona reaching consent feeds app.wa.queue.build_queue_entry (TASK-66).
5. Final assertions: >=3 consented candidates with a queue entry; mailing-list view has >=1 clinic with a resolved contact email. Report: per-persona transcript/outcome/matched-clinics/contact-status printed and written to a gitignored tests/.artifacts/ file.
6. Run for real once, capture the report, hand it back; run full offline suite to confirm nothing else broke; update docs/whatsapp.md; backlog notes/finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
New tests/test_wa_luna_e2e_funnel.py (llm-marked): _CandidateAgent plays the candidate side via its own resumed claude -p session (claude-haiku-4-5, free-text reply, cheaper than Sonnet since it is a lower-stakes roleplay task) so BOTH sides of every conversation are live, nondeterministic model calls -- unlike test_wa_luna_personas.py's intentionally fixed candidate script. 3 personas (Urkunde/Muenchen, Defizitbescheid/Augsburg, Kenntnispruefung-passed-Urkunde-pending/region-open) against a 3-clinic fixture board with one seeded clinic_contacts row, capped at 12 turns (logged, not silently passed, if unconverged). Each consenting persona feeds TASK-66's build_queue_entry; asserts >=3 consented + a mailing-list row with a resolved contact.

Real run result: all 3 personas converged in 4-5 turns (well under the 12-turn cap); the CLOSE SEQUENCE (TASK-63) executed visibly as separate turns for two of the three transcripts; mailing-list produced 9 rows (3 candidates x 3 clinics), correctly showing the one seeded clinic's contact and 'UNKNOWN' (not a fabricated address) for the other two. Full transcript report written to tests/.artifacts/e2e_funnel_report.md (gitignored) and printed.

Found and fixed one real operational bug during this run (not assumed, not a test-only issue): a live subprocess.TimeoutExpired at the old WA_LUNA_TIMEOUT_SEC=60s default on an ordinary turn -- TASK-62's MCP tool call plus effort=high add real latency the original 60s budget was set before either existed. Bumped default to 120s (app/wa/config.py), documented in docs/whatsapp.md.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
tests/test_wa_luna_e2e_funnel.py runs the whole funnel for real: a live candidate-persona agent and live Valentina, both nondeterministic, through 3 archetypes to anonymized-send consent, then into TASK-66's real queue/matching pipeline. A real run converged all 3 personas in 4-5 turns, produced a 9-row mailing list with one clinic correctly resolved to a contact and two honestly marked unknown, and surfaced a genuine production timeout bug (60s default too tight once TASK-62 added tool calls + higher effort) which is now fixed at 120s. Full transcript report is written to tests/.artifacts/e2e_funnel_report.md and printed on every run. Offline suite: 961 passed, same 6 pre-existing unrelated failures.
<!-- SECTION:FINAL_SUMMARY:END -->
