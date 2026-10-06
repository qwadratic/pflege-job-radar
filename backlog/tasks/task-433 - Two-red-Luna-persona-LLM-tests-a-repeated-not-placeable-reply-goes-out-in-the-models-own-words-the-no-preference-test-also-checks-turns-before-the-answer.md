---
id: TASK-433
title: >-
  Two red Luna persona LLM tests: a repeated not-placeable reply goes out in the
  model's own words; the no-preference test also checks turns before the answer
status: Done
assignee:
  - wa-harness
created_date: '2026-10-06 07:44'
updated_date: '2026-10-06 08:47'
labels:
  - whatsapp
  - luna
  - tests
dependencies: []
priority: medium
ordinal: 307000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The pre-deploy LLM lane on 2026-10-06 had 71 of 73 green. Two tests in tests/test_wa_luna_personas.py fail 3 of 3 runs, both at the deployed 2fe6958 and at main. (1) test_luis_reopening_with_the_identical_message_does_not_re_litigate_from_scratch: luna_brain.turn() puts the locked prompts.REJECT_BODY_DE in place only on the turn where qualification_ok first becomes False (says_not_placeable). When a not-placeable candidate writes again, the model answers explain_not_placeable in its own words. That breaks 'not placeable: explain once, then stop' (VENDORED.md) and the rule that the decline is never a model paraphrase. Fix in code: a repeated explain_not_placeable on a card that is still not placeable sends REJECT_BODY_DE verbatim; a no_send stays silent; no new German text. (2) test_no_location_preference_at_all_still_moves_the_funnel_forward: the model behaves correctly. It asks for the city on turn 2, before the candidate says 'Ist mir eigentlich egal, wo.'; on turn 3 it answers 'Prima, dann sind Sie ganz flexibel!' and moves on. The test regex scans the bubbles of all turns, so the legitimate turn-2 question fails it. Fix in the test: scan only the turns after the answer, and check that the funnel moves forward.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A not-placeable candidate who writes again gets either silence or REJECT_BODY_DE verbatim, never the model's own explanation; covered by an offline unit test of turn()
- [x] #2 The no-preference test asserts only on the turns after the candidate's no-preference answer, and checks that the reply moves the funnel forward
- [x] #3 Both LLM tests pass 3 of 3 runs; the offline lane is green, and the pre-deploy LLM lane shows no new failure
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. luna_brain.turn(): after the no_send/empty branch, a reply whose action is explain_not_placeable on a card that is still not placeable after this turn's patch sends REJECT_BODY_DE verbatim. 2. Offline unit tests for the reopened, silent and back-to-placeable cases. 3. Scope the no-preference persona test to the turns from the answer on and require a next question. 4. Sonnet build, one Opus review, apply its minors, run both LLM tests 3x and the pre-deploy LLM lane.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix in 57b47e2 (branch feat/wa-red-persona-tests). New elif in luna_brain.turn() between the no_send/empty branch and the final else: out.action == explain_not_placeable and the post-patch card is not placeable (qualification_ok False or qualification_path reject) -> [REJECT_BODY_DE]. no_send/empty still wins, so a silent repeat stays silent; a card patched back to placeable is not caught. Module docstring point 2 now says the lock holds on a repeat too. Known limit (by design, test_disqualification_is_only_overridden_once): a paraphrase sent under another action is not overridden. Opus review POSITIVE; its minors applied (test for the card clause now mutation-checked: removing the clause turns it red; redundant was_ok clause dropped; no-preference regex scans results[2:]).

Validation: offline tests/test_wa_luna_brain.py + test_wa_luna_personas.py 190 passed; pre-commit offline WA lane passed (76s). LLM: test_luis_reopening_with_the_identical_message_does_not_re_litigate_from_scratch and test_no_location_preference_at_all_still_moves_the_funnel_forward 3/3 each. tools/test_gate.py pre-deploy --target 57b47e2: offline passed, LLM lane 72/73 (log ~/.local/state/pflege-gate/logs/57b47e2...-llm.log); the one failure, test_olena_housing_is_a_plain_yes_no_first_and_the_headcount_only_after_a_yes, is unrelated (placeable card, the new branch is not reached) and passed 3/3 on rerun at the same commit. Cause: _yes_no_frames_around_options counts the synonym pair 'eine Wohnung oder Unterkunft brauchen?' as an either/or frame. Flaky test heuristic; follow-up is Ivan's call, not filed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A not-placeable candidate who writes again now gets silence or the locked REJECT_BODY_DE, never the model's paraphrase (luna_brain.turn, offline tests for the reopened, silent and back-to-placeable cases). The no-preference persona test asserts only on turns from the answer on and requires a next question. Verified: both LLM tests 3/3, offline lane green, pre-deploy LLM lane 72/73 with one unrelated flaky failure that passed 3/3 on rerun.
<!-- SECTION:FINAL_SUMMARY:END -->
