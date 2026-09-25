---
id: TASK-300
title: Never promise the candidate a colleague
status: To Do
assignee: []
created_date: '2026-09-25 00:01'
labels: []
dependencies:
  - TASK-299
priority: high
project: whatsapp
ordinal: 253000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24: 'эскалация для юзера это просто тишина... мы не обещаем человека'. Two candidate-facing strings contradict that today and both are shipped: app/wa/api.py:244 MEDIA_REPLY says 'Ein Kollege schaut sie sich an' and app/wa/luna/prompts.py:781 BLOCKED_REPLY_DE says 'Eine Kollegin schaut sich Ihre Frage an und meldet sich hier bei Ihnen'. Each is a promise the rail cannot keep -- nobody is watching that queue in real time -- and Valentyn's original complaint was exactly about being left with nothing to do next. What Ivan wants instead: answer 'хоть что-то, желательно какой-то вопрос'. Summoning a human stays real but becomes internal only (TASK-299's red status, and later a console). Note that the deterministic open question already exists in code as requirement_scoreboard's next_objective (app/wa/luna_brain.py:599-612), but in English, as a hint for the model -- the German candidate-facing wording per gate does not exist and MUST be approved by Ivan verbatim before it is written, never composed by an agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 No candidate-facing string anywhere in the repo promises that a human will look at something or get back to them
- [ ] #2 Both fallbacks render as a short acknowledgement plus the card's currently open question, keyed on the same gate order requirement_scoreboard already computes
- [ ] #3 The German wording for every gate is approved by Ivan verbatim and frozen in code with a provenance comment, in the manner of app/wa/broadcast_template.py
- [ ] #4 The not-placeable case sends the acknowledgement with no question, because the NOT PLACEABLE rule stops the checklist
- [ ] #5 Escalation still records internally and is visible to the operator
- [ ] #6 Tests assert the exact frozen strings and that no promise-a-human phrasing survives anywhere
<!-- AC:END -->
