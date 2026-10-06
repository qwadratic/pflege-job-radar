---
id: TASK-433
title: >-
  Two red Luna persona LLM tests: a repeated not-placeable reply goes out in the
  model's own words; the no-preference test also checks turns before the answer
status: To Do
assignee:
  - wa-harness
created_date: '2026-10-06 07:44'
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
- [ ] #1 A not-placeable candidate who writes again gets either silence or REJECT_BODY_DE verbatim, never the model's own explanation; covered by an offline unit test of turn()
- [ ] #2 The no-preference test asserts only on the turns after the candidate's no-preference answer, and checks that the reply moves the funnel forward
- [ ] #3 Both LLM tests pass 3 of 3 runs; the offline lane is green, and the pre-deploy LLM lane shows no new failure
<!-- AC:END -->
