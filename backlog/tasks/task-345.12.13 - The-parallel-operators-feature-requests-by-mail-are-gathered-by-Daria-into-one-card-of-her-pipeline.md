---
id: TASK-345.12.13
title: >-
  The parallel operator's feature requests by mail are gathered by Daria into
  one card of her pipeline
status: To Do
assignee: []
created_date: '2026-10-05 11:42'
updated_date: '2026-10-06 12:50'
labels: []
dependencies: []
parent_task_id: TASK-345.12
priority: medium
ordinal: 295000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05: the parallel operator answers Daria's mail and may ask for features. Daria gathers what he asks for, one card in her own backlog project "daria" (pipeline_create in tools/daria_tools.py), instead of answering each request alone or letting it get lost; Ivan reads the card. Same route as the existing rule that a change she cannot make by mail becomes a task with a number.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A mail from the parallel operator that asks for a feature or a change in how mailings, notices or Daria work is recognised by the desk and added to one open card "Parallel operator: feature requests" in her pipeline; each request keeps its date, his words and the mail it came from
- [ ] #2 Daria answers him with the card number and that the request is collected; she builds nothing and changes no config on his request alone
- [ ] #3 A question, a command or a clinic case from him is not added to the card
- [ ] #4 tests/test_daria_desk.py covers: a request creates the card, a second request is appended to it, a question is not
<!-- AC:END -->
