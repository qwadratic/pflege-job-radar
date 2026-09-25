---
id: TASK-158
title: >-
  Malteser posting fuzzy-matched to the wrong clinic (Uniklinik Würzburg) via
  Matcher.R3_tokens
status: To Do
assignee: []
created_date: '2026-09-24 23:35'
updated_date: '2026-09-25 00:10'
labels:
  - matching
dependencies: []
ordinal: 158000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-24 by TASK-142's agent while manually sampling enr_tariff/traegerart mismatches (unrelated investigation, this was a 1/15 sample finding, not the task's main subject): a posting for a Malteser-operated facility is attributed to Uniklinik Würzburg via clinic_match_rule=R3_tokens (fuzzy token-based matching), which is clearly wrong -- Malteser and Uniklinik Würzburg are unrelated operators. Not investigated further or fixed (out of TASK-142's scope); needs its own root-cause pass into why R3_tokens' token overlap accepted this pairing, and whether it's an isolated bad match or a symptom of a broader R3_tokens false-positive class (similar in spirit to TASK-100/TASK-153's other R-rule tightening work this same day).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Identify the specific posting_id(s) and the exact token overlap that caused R3_tokens to accept the Malteser/Uniklinik Würzburg pairing
- [ ] #2 Determine scope: is this an isolated bad pairing (fix the one row) or a systemic R3_tokens weakness (needs a guard, similar to TASK-100/153's rule-tightening pattern this session)
- [ ] #3 Fix and live-verify; mutation-tested regression test for whichever scope is confirmed
<!-- AC:END -->
