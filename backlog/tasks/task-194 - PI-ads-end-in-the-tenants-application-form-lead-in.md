---
id: TASK-194
title: P&I ads end in the tenant's application-form lead-in
status: To Do
assignee: []
created_date: '2026-10-01 21:01'
labels:
  - crawler
  - pi_asp
dependencies: []
ordinal: 191000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-184 adapter agent A3 (2026-10-01): after the popup read, every regiomed ad (77 of 77) ends in the form's lead-in 'Bitte beachte: ... Pflichtfelder' and every wirkzvin ad (62 of 62) ends in 'PERSOENLICHE DATEN'. They are form chrome, not ad text, and they feed the classifier and enrich_description. Rule needed: drop the last block above the first form control when it is the lead-in (the lead-in is the block directly above the first control in AD_JS). Not built in A3 because it needs a decision on how to tell a lead-in from a short last paragraph of the ad.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 AD_JS or its caller drops the form lead-in block on regiomed and wirkzvin; 77 of 77 and 62 of 62 stored ads no longer end in it
- [ ] #2 A real ad whose last block is a short closing paragraph keeps it (frozen fixture of such an ad)
- [ ] #3 Mutation check: with the strip removed the lead-in test fails
<!-- AC:END -->
