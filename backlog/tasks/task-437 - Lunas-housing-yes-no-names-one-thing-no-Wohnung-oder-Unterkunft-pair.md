---
id: TASK-437
title: 'Luna''s housing yes/no names one thing: no "Wohnung oder Unterkunft" pair'
status: Done
assignee:
  - wa-harness
created_date: '2026-10-06 08:54'
updated_date: '2026-10-06 09:05'
labels:
  - whatsapp
  - luna
dependencies: []
priority: medium
ordinal: 313000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The pre-deploy LLM lane on 2026-10-06 (gate on 57b47e2) had one flaky failure, test_olena_housing_is_a_plain_yes_no_first_and_the_headcount_only_after_a_yes. It failed once in four runs. Luna asked "Würden Sie ... eine Wohnung oder Unterkunft brauchen?". The test's yes/no-frame check counts any 'oder' pair as an either/or frame. The HOUSING rule in app/wa/luna/prompts.py says "whether they need a flat (Unterkunft)". That parenthetical invites the two-noun pair. Ivan 2026-10-06: fix it in the prompt, not by loosening the test. A synonym list in the test would be a hand-written German phrase list. Fix: the HOUSING rule names one noun and forbids two nouns joined by 'oder' in that question. Requested by the WhatsApp lane (wa-harness).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The HOUSING rule asks about one noun only and refers to YES/NO QUESTIONS
- [x] #2 The offline prompt tests are green
- [x] #3 The two Olena housing LLM tests (the housing yes/no-first test and the housing_gate case of test_olena_city_and_housing_questions_are_not_yes_no_frames_around_options) pass 10 of 10 runs each
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Name the flat with one noun in both places the model reads: the HOUSING rule in app/wa/luna/prompts.py and the housing next_objective label in luna_brain's requirement scoreboard (both said 'a flat (Unterkunft)'). Update the offline assertions; run the two Olena housing LLM tests 10x each.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Both model-facing sources said 'whether they need a flat (Unterkunft) at all'; the parenthetical invited 'eine Wohnung oder Unterkunft'. Now: prompts.py HOUSING says 'naming it with ONE noun (eine Wohnung) -- never two nouns joined by oder in that question ... (YES/NO QUESTIONS)'; the scoreboard label says 'naming it with one noun'. The bad pair is not quoted in the prompt (no negative priming); the test heuristic is unchanged. Validation: offline tests/test_wa_luna_brain.py + personas 187 passed, offline tests/test_wa_*.py 1962 passed. LLM: test_olena_housing_is_a_plain_yes_no_first_and_the_headcount_only_after_a_yes and test_olena_city_and_housing_questions_are_not_yes_no_frames_around_options[housing_gate] 10/10 each (/dev/shm/task437-llm10x.log). The full LLM lane runs in the pre-deploy gate.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Luna's housing yes/no now names the flat with one noun: the HOUSING prompt rule and the scoreboard's housing objective no longer say 'a flat (Unterkunft)', which invited the 'Wohnung oder Unterkunft' pair the either/or check flags. Fixed in the prompt, not by loosening the test. Verified: offline WA tests green; both Olena housing LLM tests 10/10.
<!-- SECTION:FINAL_SUMMARY:END -->
