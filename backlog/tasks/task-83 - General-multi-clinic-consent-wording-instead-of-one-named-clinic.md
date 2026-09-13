---
id: TASK-83
title: General multi-clinic consent wording instead of one named clinic
status: Done
assignee: []
created_date: '2026-09-13 12:19'
updated_date: '2026-09-13 12:28'
labels: []
dependencies: []
ordinal: 83000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan: the consent ask (with buttons, TASK-80) currently names one specific clinic when the shortlist happens to have just one entry, but the actual matching step afterward (build_queue_entry) always ranks against the whole live clinic snapshot -- so a candidate's consent scope did not honestly match what the system actually does with it. Reword the consent question to always be general (Bavarian clinics matching the profile), never tied to one named clinic, regardless of shortlist size.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 prompts.py's consent-ask rule requires general phrasing (matching Bavarian clinics), never a single named clinic, even when the shortlist has exactly one entry
- [x] #2 The model may still reference the shortlist by name in the same breath, as long as the actual permission asked for is general
- [x] #3 No code/schema change needed since build_queue_entry already matches against the whole snapshot regardless of what was named out loud -- consent wording now honestly reflects existing behavior
- [x] #4 Live persona run confirms the new phrasing
- [x] #5 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Live-verified on the next e2e run: all 3 personas got 'Darf ich Ihr anonymisiertes Profil ... an bayerische Kliniken weiterleiten, die zu Ihrem Profil passen -- darunter auch das Klinikum X?' -- general framing, named clinic only as an example, exactly matching build_queue_entry's actual multi-clinic matching behavior.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Consent wording now matches actual system behavior: the model asks permission to share with matching Bavarian clinics generally (plural), never scoped to one named clinic, since the real downstream matching step (build_queue_entry) always fans out to the whole live snapshot regardless of what got named out loud during the shortlist step. Pure prompt fix, no schema or matching-logic change needed.
<!-- SECTION:FINAL_SUMMARY:END -->
