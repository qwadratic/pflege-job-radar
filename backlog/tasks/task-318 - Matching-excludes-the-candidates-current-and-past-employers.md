---
id: TASK-318
title: Matching excludes the candidate's current and past employers
status: To Do
assignee: []
created_date: '2026-09-26 13:16'
labels: []
dependencies: []
priority: medium
ordinal: 260000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's standing rule (2026-09-26): a candidate is never offered to a clinic she works at now or has worked at before. Today candidate-to-clinic matching (app/autopilot/matching.py rank/score, and the search_postings path the WhatsApp agent uses) ranks those clinics like any other. Found on the nurse-79 email case: her current employer (Uniklinikum Erlangen) and past employers (Fachklinik Herzogenaurach, Klinikum Fürth, which she named only in chat) came out of the board match as candidates. Employers come from the CV experience rows and from employers the candidate names in chat.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A clinic that is the candidate's current employer is absent from every match list the harness produces for her
- [ ] #2 A clinic that is any past employer (CV experience row or an employer she named in chat) is absent in the same way
- [ ] #3 The exclusion is recorded per clinic with its source (CV row or message id), so an operator can see why a clinic is missing
- [ ] #4 Tests cover a current employer, a CV past employer, a chat-only past employer, and a sister clinic of the same Träger that stays in
<!-- AC:END -->
