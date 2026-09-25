---
id: TASK-302
title: >-
  The warming turn: answer primary interest with a real vacancy, a count, and
  the next question
status: To Do
assignee: []
created_date: '2026-09-25 00:01'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 255000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24, described as 'прогревающий ход'. Once a candidate has shown primary interest and the city is known, the next reply should not be another bare question. It should be three bubbles: a media bubble carrying the best-matching vacancy, a bubble saying how many were found in total, and a bubble with the next question that advances the card. The third bubble should remember what we already hold about this person -- his example: 'помню резюме, оно актуально или пришлёте новое' rather than asking for a CV as if we had never met. The Haiku closing gate already guarantees the last bubble hands the turn back, so the warming content cannot cost us forward motion. This is his second success criterion: bubbles enriched with genuinely useful information instead of interrogation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Triggered once primary interest is established and the city is known, from the card, not from the model's judgement
- [ ] #2 The reply is an array of bubbles whose media bubble carries the best-matching vacancy for that card
- [ ] #3 One bubble states how many matches were found in total
- [ ] #4 The final bubble asks the next card-advancing question and references documents already held rather than asking blind
- [ ] #5 The closing gate passes the array, and the last bubble is the question
- [ ] #6 Nothing in the warming turn states a salary figure or any fact not present in the board data
<!-- AC:END -->
