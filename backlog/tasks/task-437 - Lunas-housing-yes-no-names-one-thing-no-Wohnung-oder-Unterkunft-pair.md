---
id: TASK-437
title: 'Luna''s housing yes/no names one thing: no "Wohnung oder Unterkunft" pair'
status: To Do
assignee:
  - wa-harness
created_date: '2026-10-06 08:54'
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
- [ ] #1 The HOUSING rule asks about one noun only and refers to YES/NO QUESTIONS
- [ ] #2 The offline prompt tests are green
- [ ] #3 The two Olena housing LLM tests (the housing yes/no-first test and the housing_gate case of test_olena_city_and_housing_questions_are_not_yes_no_frames_around_options) pass 10 of 10 runs each
<!-- AC:END -->
