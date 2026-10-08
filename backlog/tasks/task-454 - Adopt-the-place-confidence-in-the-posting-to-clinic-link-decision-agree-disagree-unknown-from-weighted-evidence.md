---
id: TASK-454
title: >-
  Adopt the place confidence in the posting-to-clinic link decision (agree,
  disagree, unknown from weighted evidence)
status: To Do
assignee: []
created_date: '2026-10-08 12:07'
labels:
  - matching
  - data-quality
  - crawler-coverage
dependencies: []
priority: high
ordinal: 336000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-08: try the degree of confidence in the real matcher, not only as an experiment. Source: the additive experiment of TASK-431.9 (branch exp/place-confidence: pflege_jobs/place_conf.py, weights per kind of evidence for postings and clinics, level factors PLZ/municipality/Kreis, the board-stamp check; findings: of 3834 compared links 278 disagreed, 7.3 percent; after the point fixes of PR #27 and the corrections of 2026-10-06/07, 36 still disagree on the mirror). Today the Matcher (pflege_jobs/registry.py) decides by rules (R0..R6) and the experiment only measures. Wanted: the link decision uses the confidence, so a link whose posting place and clinic place disagree on weighted evidence is refused (the posting stays unlinked with its own city), and an unknown place changes nothing. Example that the point fixes do not cover: a posting whose address says one town and whose stored city is another, linked by employer tokens to a same-name clinic. Dependents of the place values to keep consistent: pflege_jobs/geo.py in_bavaria, app/data.py clinic_centroid and the city search (matches the posting city OR the clinic town), link-clinics, tools/reverify_and_clean.py, app/data.py JOB_COLS. No invented thresholds or caps: the classes and weights are those of the experiment unless Ivan changes them; a refusal is recorded with the reason, never silent. Related: TASK-431.9 (parked by Ivan 2026-10-08, its open ACs continue here), TASK-448 (cities), TASK-441 (provenance).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Red tests on the mirror first: a posting whose evidence disagrees with the clinic place is refused by the Matcher, an unknown place is not refused, an agreeing one is linked as before
- [ ] #2 Before and after on the mirror: agree, disagree, unknown counts and the links that change, by rule, with the disagreeing links listed with their evidence
- [ ] #3 The refusal is recorded with its reason and confidence in the match output; nothing is silent
- [ ] #4 A dry-run set of stored-link corrections is produced; nothing is written to the database before Ivan approves the exact counts
- [ ] #5 The dependents listed in the description are checked and either unchanged or changed with a test
<!-- AC:END -->
