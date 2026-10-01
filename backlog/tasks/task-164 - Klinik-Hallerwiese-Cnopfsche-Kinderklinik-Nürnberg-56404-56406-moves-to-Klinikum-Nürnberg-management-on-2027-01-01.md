---
id: TASK-164
title: >-
  Klinik Hallerwiese-Cnopfsche Kinderklinik Nürnberg (56404/56406) moves to
  Klinikum Nürnberg management on 2027-01-01
status: To Do
assignee: []
created_date: '2026-09-28 06:46'
labels: []
dependencies:
  - TASK-17
ordinal: 162000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-28 while fixing TASK-163 (clinic 56404/56406 board cleanup): posting 7087's own live ad text ('Pflegefachkraft (m/w/d) für die Intensivstation') states the clinic transitions to Klinikum Nürnberg management effective 01.01.2027. This is exactly the kind of clinic-level structural change TASK-17's planned clinic_events mechanism is meant to record and flag (rename/merger/closure/operator-change, with evidence and a severity), but that mechanism doesn't exist yet and this finding came from a crawled posting's own text, not a Krankenhausplan PDF diff -- a different discovery channel than TASK-17's scope, same underlying need. No action taken on employer/clinic_id mapping yet; recording the evidence now so it isn't lost before the date arrives.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Before 2027-01-01 (or when TASK-17's clinic_events mechanism lands, whichever first): decide how 56404/56406's employer/operator mapping should change -- new clinic_id under Klinikum Nürnberg, an operator field update, or a clinic_events row once that table exists
- [ ] #2 Re-verify live closer to the date: is the transition still on track, did it happen, did the board/URL structure change as a result
<!-- AC:END -->
