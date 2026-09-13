---
id: TASK-73
title: Luna proactively names what it searched for when it uses a tool
status: Done
assignee:
  - '@claude'
created_date: '2026-09-13 09:40'
updated_date: '2026-09-13 09:51'
labels: []
dependencies: []
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked: whenever the candidate mentions something that lets Luna filter/search (a city, department, housing need, etc.), it should actually call the tool AND say so in its reply when relevant to the conversation -- not just silently use the result. TASK-62 already made tool use proactive; this is a transparency refinement on top, prompt-level not code-level.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 prompts.py TOOLS rule is extended: after a tool call driven by something the candidate just said, the reply should reflect what was searched/found in natural language when it helps the candidate understand the answer (e.g. naming the city/department it checked), without turning into a mechanical announcement every single turn
- [x] #2 Persona/live tests demonstrate at least one case where a tool-driven answer explicitly references what was searched
- [ ] #3 Full offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Extend prompts.py TOOLS rule: after a tool call driven by something the candidate just said, weave what was searched/found into the reply in natural language (e.g. naming the city/department checked) when it helps them understand the answer -- not a mechanical announcement every turn.
2. Verify live against the real CLI with a persona mentioning a specific city/department, confirming the reply references what was checked.
3. Offline suite, backlog finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Extended prompts.py TOOLS rule with a NAME WHAT YOU CHECKED clause -- weave the searched city/department/region into the sentence naturally rather than a separate mechanical announcement, and only when a real tool call happened (not for plain market_snapshot facts). Strengthened the existing Coburg proactive-tool-use test to assert the reply actually names the city, not just that it answers. Verified live: 'In Coburg habe ich aktuell leider keine offene Stelle im Bestand' -- already satisfied this before the change, confirmed to keep doing so.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
prompts.py's TOOLS rule now explicitly asks Luna to name what it checked (the city/department/clinic a tool call was driven by) as part of its natural reply, not a bolted-on announcement, and only when a real tool call happened. Verified live and via a strengthened existing test (test_a_question_about_an_unlisted_city_actually_triggers_a_live_search now asserts the reply names the checked city). Offline suite: 1013 passed, 5 pre-existing unrelated failures.
<!-- SECTION:FINAL_SUMMARY:END -->
