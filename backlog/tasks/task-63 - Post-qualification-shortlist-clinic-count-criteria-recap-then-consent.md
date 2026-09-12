---
id: TASK-63
title: 'Post-qualification shortlist, clinic count, criteria recap, then consent'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-12 16:00'
updated_date: '2026-09-12 16:02'
labels: []
dependencies: []
ordinal: 63000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Once a candidate is qualified today, Luna has no defined close: card_patch already has anonymous_send_consent/anonymous_send_offered/pflege_matches_sent fields, but nothing in prompts.py sequences the actual conversation trajectory Ivan wants -- state how many clinics were found, name a short concrete list, restate the matched criteria so the candidate can correct it, and only then ask for consent to send an anonymized profile. See /home/claude/.claude/plans/wise-enchanting-crayon.md section 2.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 market_snapshot() in app/wa/luna_brain.py returns a distinct-clinic shortlist (up to 5) and a matching_clinics_count (distinct clinics, not job count), available once qualification_ok, city, department_pref and housing_known are all satisfied
- [ ] #2 prompts.py THINK_ORDER/RULES sequence the close as four separate turns: total clinic count, then the shortlist, then a one-line criteria recap, then the consent question -- never combined in one bubble
- [ ] #3 Persona tests (Maria and Yassine reaching this stage) assert the count, shortlist and recap appear in separate turns strictly before the consent question
- [ ] #4 Full offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Extend market_snapshot() in luna_brain.py: add shortlist (top 5 distinct clinics) and matching_clinics_count (distinct clinic count), gated on qualification_ok+city+department_pref+housing_known all satisfied.
2. Extend prompts.py THINK_ORDER step 7 + RULES: sequence count -> shortlist -> criteria recap -> consent ask as four separate turns, never combined.
3. Extend Maria/Yassine persona tests (tests/test_wa_luna_personas.py) to assert the sequence.
4. Run offline suite + relevant llm persona tests, update docs, backlog notes/finalize.
<!-- SECTION:PLAN:END -->
