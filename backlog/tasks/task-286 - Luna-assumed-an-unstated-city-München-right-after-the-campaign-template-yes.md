---
id: TASK-286
title: Luna assumed an unstated city (München) right after the campaign template yes
status: Done
assignee: []
created_date: '2026-09-23 21:18'
updated_date: '2026-10-06 12:49'
labels:
  - whatsapp
  - prompt
dependencies: []
ordinal: 239000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live UAT, 2026-09-23: Ivan replied Ja to the resent campaign template on the parallel operator test thread. Luna's next reply referenced looking at positions in München before the parallel operator had named any city -- he had only said Bayern (via the template's own wording). Diagnosed as a prompt-behavior gap, not a stale-card/data-leak bug (the card was built fresh in this conversation, verified empty just before). Root cause: app/wa/luna/prompts.py's CAMPAIGN rule (the 'yes to the template' branch) told the model to set region=Bayern and ask the next qualification gate, but said nothing forbidding it from naming a specific city before card.city was ever set. Existing city-scoped rules (DEPARTMENT at the time, ~line 254; the housing rule 'never name a city or clinic that is not in that data') only covered narrower contexts, not this general opener.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 CAMPAIGN rule's yes-to-template branch explicitly forbids naming or assuming a specific city before card.city is set from the candidate's own words
- [x] #2 Fix references the live finding for traceability
- [x] #3 tests/test_wa_luna_dialog_rules.py passes with the new rule text in place
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Edited app/wa/luna/prompts.py CAMPAIGN rule (yes-to-template branch): added an explicit clause forbidding naming/assuming a specific city, citing the live 2026-09-23 München finding, pointing to card.city as the only source of truth. tests/test_wa_luna_dialog_rules.py: 174 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a one-clause guard to the CAMPAIGN rule in app/wa/luna/prompts.py so a yes to the campaign template never lets the model name or assume a city (e.g. München) before the candidate has stated one -- verified via tests/test_wa_luna_dialog_rules.py (174 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
