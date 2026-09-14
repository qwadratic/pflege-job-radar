---
id: TASK-94
title: Follow-up nudges must not fire on finished threads (consented / not placeable)
status: Done
assignee: []
created_date: '2026-09-14 09:38'
updated_date: '2026-09-14 13:28'
labels: []
dependencies: []
type: bug
ordinal: 94000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found live 2026-09-14 on the manual test number (Ivan manual test): the thread ended 2026-09-13 21:16 UTC with consent tapped and Luna saying a colleague will reach out. app/wa/luna/followups.py only checks ball_for()=="them" (we sent last), so it sent "sind Sie noch da? Ich helfe gerne weiter" at 07:07 and again, identical, at 08:07 UTC; tier 2 was due ~12:08 UTC. A finished thread always ends with our message, so every consented or not-placeable candidate would get 3 nonsense nudges.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 followups.run() sends nothing to a thread whose reporting.stage_for(card) is "consented" or "not_placeable", even when ball=them and a tier is due
- [x] #2 threads in any other stage (e.g. waiting on a requested document) are still nudged exactly as before
- [x] #3 offline tests cover both terminal stages and pass; full offline WA test suite stays green
- [x] #4 docs/whatsapp.md follow-up paragraph states the terminal-stage exclusion
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. In followups.run(), skip a thread when REP.stage_for(t['slots']) is in a module-level TERMINAL_STAGES=('consented','not_placeable'), checked right after the stopped/ball checks, before tier/claim.
2. Tests: consented thread with a due tier -> no send; not_placeable thread -> no send; existing tests unchanged.
3. docs/whatsapp.md TASK-85 paragraph: one line on the exclusion.
4. Run WA offline tests.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
followups.run() now skips threads whose reporting.stage_for(card) is in TERMINAL_STAGES=('consented','not_placeable'), checked after stopped, before ball/tier/claim. Tests: parametrized consented/not_placeable thread with tier due -> no send; qualified thread waiting on a requested document -> tier 0 still sent. Mutation check: emptying TERMINAL_STAGES makes both terminal tests fail (2 failed). Live DB read-only check: the manual test number stage=consented -> skipped; a scoped run for that phone sent 0. Full offline suite: 1171 passed, 126 skipped (tests/test_completeness_dvinci.py ignored, network collection error). Oneshot timer picks up the change on the next tick, no restart. Not done (not requested): per-tier nudge texts, overnight-deferral wording, MAX_FOLLOWUPS_PER_STREAK=4 > 3 tiers.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Follow-up nudges no longer fire on finished threads (consented or not placeable); found live when a consented test candidate got 'sind Sie noch da?' twice. Verified with 3 new tests (plus mutation check), a read-only check on the live thread, and the full offline suite (1171 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
