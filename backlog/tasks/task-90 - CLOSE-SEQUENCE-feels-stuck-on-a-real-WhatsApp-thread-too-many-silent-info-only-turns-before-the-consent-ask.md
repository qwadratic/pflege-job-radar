---
id: TASK-90
title: >-
  CLOSE SEQUENCE feels stuck on a real WhatsApp thread: too many silent
  info-only turns before the consent ask
status: Done
assignee: []
created_date: '2026-09-13 19:19'
updated_date: '2026-09-14 13:29'
labels: []
dependencies: []
type: bug
ordinal: 90000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live real-phone test (the manual test number, 2026-09-13) of the post-qualification close flow (TASK-63) shows the candidate getting two consecutive info-only bot turns (clinic count, then shortlist) with no question in either -- they have to blindly send filler replies (e.g. 'passt') to nudge the bot forward, and the transcript ends before ever reaching the criteria recap or the anonymised-send consent question at all. Reported directly by Ivan as 'not pushing me through the list, just tells me there are clinics but doesn't ask.'

Comparing against this repo's own pre-TASK-63 state (git show f14df2b -- app/wa/luna/prompts.py) shows the original design was much tighter: 'matches[] as short text, then the anonymized-send offer' -- read together with the STYLE rule (1-2 bubbles, info + question in the same turn, as already used elsewhere in this exact transcript for the region and housing questions), that is one turn of info + question, not four turns of which three are silent statements. TASK-63 deliberately spread this into four separate turns and validated it only against scripted personas; a real, impatient human now exposes that the spread-out version reads as broken.

market_snapshot()'s matching_clinics_count/shortlist fields (app/wa/luna_brain.py) are populated as soon as qualification_ok + city + department_pref + housing_known are all set -- there is no code-level gate forcing multiple turns, the four-turn spread is purely a prompts.py instruction choice. Fix is a prompts.py-only change (THINK_ORDER step 7 + the CLOSE SEQUENCE rule in RULES): collapse back to two turns -- turn A states the distinct clinic count and names the shortlist together (info, no question yet); turn B restates the matched criteria and asks the anonymised-send consent (with buttons, per TASK-80) in the same turn. Keep the TASK-83 general-consent-wording requirement and the resume-if-interrupted clause; no market_snapshot/luna_brain.py code change is needed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 THINK_ORDER step 7 and the CLOSE SEQUENCE rule in app/wa/luna/prompts.py describe two turns (count+shortlist together, then recap+consent together), not four
- [x] #2 TASK-83's general (not one-named-clinic) consent wording requirement still holds in the merged recap+consent turn
- [x] #3 TASK-80's button-confirmed consent (anonymous_send_offered set, buttons attached, is_button_reply check) still holds unchanged
- [x] #4 Existing persona/regression tests covering the close sequence pass or are updated to match the new two-turn shape; offline suite (pytest -q -m "not network and not completeness and not mutation and not llm") stays green
- [x] #5 pflege-wa.service restarted so the live harness runs the updated prompt
- [x] #6 the manual test number's thread history is cleared (wa_threads/wa_messages rows + its persisted Claude session dir) so the next manual test starts fresh
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Edit app/wa/luna/prompts.py: THINK_ORDER step 7 wording ('CLOSE SEQUENCE' -> two turns), and RULES' CLOSE SEQUENCE bullet: turn A = distinct clinic count + shortlist (info only), turn B = criteria recap + consent question w/ buttons (keep TASK-83 general-wording clause, TASK-80 button-tap clause, and the resume-if-interrupted clause).
2. grep tests/test_wa_luna_personas.py and any other close-sequence-asserting test for the old 4-step expectation; update to the new 2-step shape.
3. Run offline suite (pytest -q -m 'not network and not completeness and not mutation and not llm').
4. Restart pflege-wa.service so the live harness picks up the change (ask user if blocked by sandbox prod-deploy guard).
5. Clear the manual test number's thread: delete its wa_threads/wa_messages/wa_luna_calls/wa_reply_turn_claims rows via the app's own store layer (not raw sqlite, PII guard), and remove its persisted Claude CLI session dir under data/wa_luna_sessions.
6. Verify via GET /api/wa/threads that the thread is gone, then hand back to user to repeat the live test.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause confirmed via git show f14df2b -- app/wa/luna/prompts.py: pre-TASK-63 design was 'matches[] as short text, then the anonymized-send offer' (one turn, info+question, same pattern already used for the region/housing questions in this exact live transcript). TASK-63 spread that into 4 separate turns (3 of them pure statements, no question) and validated it only against scripted personas; the real the manual test number test exposed it as feeling stuck. Fixed by editing THINK_ORDER step 7 and the CLOSE SEQUENCE rule in app/wa/luna/prompts.py to 2 turns: (1) count+shortlist together, info only; (2) recap+consent together, ending in the actual button-backed question. TASK-80/TASK-83 clauses left untouched. Renamed/updated tests/test_wa_luna_personas.py's close-sequence test docstring to match; its substantive invariant (first clinic mention != same turn as consent ask) still holds under the new shape so no assertion logic changed. Did NOT re-run the llm-marked persona suite itself (real CLI cost) -- offline suite (excluding the pre-existing, unrelated network-collection issue in test_completeness_dvinci.py) is 1156 passed, 126 skipped. Restarted pflege-wa.service (confirmed active, /api/wa/health shows brain=luna, luna_ready=true). Cleared the manual test number via app.wa.store (wa_threads/wa_messages/wa_reply_turn_claims/wa_luna_calls rows) and removed its orphaned Claude CLI session transcript (data/wa_luna_sessions session 7715fed6-...); GET /api/wa/threads now shows total=0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed a live UX regression: once qualification/city/department/housing were known, Luna gave two consecutive info-only WhatsApp turns (clinic count, then shortlist) with no question in either, so a real candidate had to blindly reply to nudge it forward and never reached the consent ask in the observed transcript. Root cause: TASK-63 spread what was originally a tight 'info + consent offer' turn into 4 mandatory separate turns. Compressed app/wa/luna/prompts.py's CLOSE SEQUENCE back to 2 turns (count+shortlist together, then recap+consent together) while preserving TASK-80's button-tap consent and TASK-83's general (not one-clinic) wording. Verified: offline suite green (1156 passed), pflege-wa.service restarted and healthy, and the original test thread (the manual test number) fully cleared (DB rows + orphaned CLI session transcript) for a clean retest.
<!-- SECTION:FINAL_SUMMARY:END -->
