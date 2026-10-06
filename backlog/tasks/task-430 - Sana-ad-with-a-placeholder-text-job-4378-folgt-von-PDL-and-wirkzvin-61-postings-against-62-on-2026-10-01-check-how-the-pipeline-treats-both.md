---
id: TASK-430
title: >-
  Sana ad with a placeholder text (job 4378, folgt von PDL) and wirkzvin 61
  postings against 62 on 2026-10-01: check how the pipeline treats both
status: To Do
assignee: []
created_date: '2026-10-06 06:16'
labels:
  - crawler-coverage
  - data-quality
dependencies: []
priority: low
ordinal: 300000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-197 mirror re-record on 2026-10-05. (1) Job 4378 on jobs.sana.de carries the placeholder ad text folgt von PDL (to follow from the nursing director): a posting without a real description. Decide how it is classified and shown (the no-description bucket of TASK-184) and whether a test on the mirror pins it. (2) wirkzvin lists 61 postings in the recording of today and listed 62 on 2026-10-01: find out whether one posting closed (normal) or the adapter dropped one, with the two recordings as evidence.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Job 4378: the handling of a placeholder-only ad is decided, written down and pinned by a test on the mirror
- [ ] #2 The wirkzvin difference is explained with evidence from both recordings: closed posting or adapter drop; if a drop, a red test and a fix
<!-- AC:END -->
