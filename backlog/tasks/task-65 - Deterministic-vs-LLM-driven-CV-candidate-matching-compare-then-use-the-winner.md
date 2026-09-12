---
id: TASK-65
title: >-
  Deterministic vs LLM-driven CV/candidate matching: compare, then use the
  winner
status: To Do
assignee: []
created_date: '2026-09-12 16:00'
labels: []
dependencies: []
ordinal: 65000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked to compare the existing deterministic regex/keyword CV matcher (app/cv.py) against a non-deterministic LLM-driven one before deciding which to use for real WhatsApp candidates -- not to assume the LLM path is better. The repo already has a small eval harness for exactly this (evals/cv/run.py against evals/cv/cases/*.json) with only two cases today. See /home/claude/.claude/plans/wise-enchanting-crayon.md section 7. This task result determines which extraction path TASK-66 CV/Urkunde intake wires up.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 evals/cv/cases/ is widened with additional synthetic, genericized cases covering the qualification-path variety already used in the persona tests
- [ ] #2 A new LLM-driven extraction path (for example CV.analyse_llm) produces the same {profile, matches} shape as CV.analyse, reasoning via the claude CLI over CV text and chat history
- [ ] #3 evals/cv/run.py can run either path over the same case set and both results are compared for pass rate and match quality
- [ ] #4 The comparison result and the chosen path (deterministic, LLM, or an explicit hybrid) are documented in the eval output or a short note, not left as an unstated assumption
- [ ] #5 The widened case set passes against the chosen path via python evals/cv/run.py; an llm-marked test asserts the LLM path pass rate too
- [ ] #6 Full offline suite stays green
<!-- AC:END -->
