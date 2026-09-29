---
id: TASK-132
title: >-
  _match_jd (R_jd_text) has no town/city cross-check, unlike every other
  board-adjacent rule
status: To Do
assignee: []
created_date: '2026-09-23 14:43'
updated_date: '2026-09-25 00:10'
labels:
  - matching
dependencies: []
priority: low
type: bug
ordinal: 132000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-101/TASK-131 follow-up (independent verify agent's own recommendation, 2026-09-23). Every OTHER Matcher rule that can act on a fuzzy/partial signal cross-checks the posting's own city before trusting it: R1_exact/R1_exact_town (via other_town_disagrees), R0_board_name/R0_board_town/R0_board_tokens (decision-5/TASK-59a). _match_jd is the only content-side rule with zero town awareness -- it trusts a unique name/operator token hit registry-wide regardless of what city the posting itself states.\n\nTASK-131's parse_quality='partial' guard closes the ONE concretely reproduced case (18872's corrupted town field) but is a targeted patch for corrupted registry data, not a structural defense. A clean-but-wrong-city candidate (two real, correctly-parsed clinics that happen to share enough name/operator vocabulary, in different towns) could still slip through _match_jd today with no town check at all -- not yet reproduced live, but the gap is real and cheap to close.\n\nNote before implementing: decision-5 (backlog/decisions/decision-5) explicitly states content-match rules (R1/R2) 'must not be gated on board/city agreement, by the same reasoning R1/R2 aren't' -- _match_jd is a content-match rule in the same _match_content() method. Adding a city check here is a deliberate EXCEPTION to that standing principle (justified because _match_jd's substring-subset evidence is structurally weaker/fuzzier than R1/R2's exact-identity match, closer in kind to R0_board's provenance-based trust), not an oversight in decision-5 -- this task should either get sign-off as a formal amendment to decision-5, or record a new decision documenting why _match_jd is the exception.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 _match_jd refuses a hit when the posting's own known city disagrees with the matched candidate's town, mirroring R0_board_town's _town_match() shape (ck falsy still passes through unchanged, same as every other town-aware rule)
- [ ] #2 A red-green test reproduces a same-vocabulary-different-town collision (two synthetic clinics whose name/operator tokens both clear the >=2 floor, different towns) and confirms the city check refuses it where the un-guarded rule would have picked one arbitrarily
- [ ] #3 Full local replay against data/inbox.sqlite (same method TASK-101 used) shows this change causes zero additional unintended clinic_id flips beyond what TASK-101/TASK-131 already accounted for
<!-- AC:END -->
