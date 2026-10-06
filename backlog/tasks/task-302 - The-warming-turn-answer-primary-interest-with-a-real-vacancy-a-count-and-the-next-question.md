---
id: TASK-302
title: >-
  The warming turn: answer primary interest with a real vacancy, a count, and
  the next question
status: Done
assignee: []
created_date: '2026-09-25 00:01'
updated_date: '2026-09-25 17:15'
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
- [x] #1 Triggered once primary interest is established and the city is known, from the card, not from the model's judgement
- [x] #2 The reply is an array of bubbles whose media bubble carries the best-matching vacancy for that card
- [x] #3 One bubble states how many matches were found in total
- [x] #4 The final bubble asks the next card-advancing question and references documents already held rather than asking blind
- [x] #5 The closing gate passes the array, and the last bubble is the question
- [ ] #6 Nothing in the warming turn states a salary figure or any fact not present in the board data
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-25. Built in 4 commits (a08eb34, 5727922, adbe909, b47fb6c), merged into feat/whatsapp-harness and deployed (pflege-wa restart, brain on claude-sonnet-5 max). Design after Ivan's review round: code computes a top-10 shortlist with the brain's own search functions (city resolution, role class, department_pref, housing) incl. full description and link; the model picks one (warming_pick/why), code validates the pick is in the shortlist; no city match widens by haversine radius (WA_LUNA_WARMING_RADIUS_KM, default 30), still nothing -> ask to drop a criterion. Warming keys are code-owned and written only on success; decline/no-match/failure recorded visibly. Title/description words are grounded turn-only (never GROUNDED_KEY); the picked posting's words re-grounded on later turns. Role class now reaches every count tool. Salary: Ivan reversed the ban 2026-09-25 -- the model may quote pay as the posting text states it, so AC #6's salary clause is superseded. Opus 5.5 review round 1 REJECTED (title word poisoning thread memory, different row set than the tools, stamp before the model ran) -- all three fixed and covered by regression tests. Live checks (Sonnet 5 max, no_send, scratch DB): warming turn and the follow-up 'Ist die Stelle noch frei?' both pass first try; CV-held case asks for the Urkunde referencing the CV. Radius and drop-a-criterion paths verified offline only. Scoped suite 1101 passed; 27 pre-existing PostgREST-401 failures unchanged.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Warming turn live: a code-picked shortlist of 10 real postings, the model presents the best fit, the role-filtered total and the next card-advancing question in three bubbles; radius widening and a drop-a-criterion question when nothing matches. Verified by offline regression tests and live two-turn runs on Sonnet 5; the per-turn fact filter is being replaced by a correction turn at lead close (separate build).
<!-- SECTION:FINAL_SUMMARY:END -->
