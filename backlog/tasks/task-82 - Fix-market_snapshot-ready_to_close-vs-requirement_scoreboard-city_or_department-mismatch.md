---
id: TASK-82
title: >-
  Fix market_snapshot ready_to_close vs requirement_scoreboard
  city_or_department mismatch
status: Done
assignee: []
created_date: '2026-09-13 12:19'
updated_date: '2026-09-13 13:13'
labels: []
dependencies: []
ordinal: 82000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live e2e persona run (TASK-74 follow-up) found a candidate genuinely flexible on department stalled indefinitely: requirement_scoreboard told the model city_or_department was 'satisfied' (its own OR semantics), but market_snapshot's ready_to_close silently required BOTH city AND department_pref, so the shortlist/count never populated and the close sequence never triggered.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 market_snapshot's ready_to_close and requirement_scoreboard's city_or_department share one predicate, cannot silently diverge again
- [x] #2 A candidate with only city known (no department preference) reaches a populated shortlist
- [x] #3 A candidate with only department known (no city preference) reaches a populated shortlist
- [x] #4 Regression tests cover both cases plus the neither-known case
- [x] #5 Full offline suite stays green
- [x] #6 Live e2e persona run confirms a flexible-on-department persona now converges
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/luna_brain.py: new _city_or_department_satisfied(card) helper (city OR department_pref), used by both requirement_scoreboard's city_or_department gate and market_snapshot's ready_to_close -- previously the latter silently required AND. Docstrings and prompts.py's CLOSE SEQUENCE rule updated to describe EITHER, not ALL. 4 new tests. Live-verified: the mai_kenntnispruefung persona (flexible department) converged in 7 turns on the next e2e run, showing a correctly-populated 3-clinic shortlist.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
One-line root cause, real product impact: a candidate who genuinely has no department preference (a valid, common answer) could never reach the consent step at all before this fix, because two functions disagreed about whether that counted as 'answered'. Found via a live two-agent conversation, not a written test -- exactly the kind of bug a fully-scripted test suite would not have surfaced.
<!-- SECTION:FINAL_SUMMARY:END -->
