---
id: TASK-18
title: >-
  Experiment: route group boards by ats_type instead of a name-matched group
  short-circuit
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
labels:
  - harvester
dependencies: []
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Today app/crawl.py checks group_portal_for() before consulting ats_type, and GROUP_PORTALS matches kbo and Barmherzige Brüder by clinic name regex. That is why the 11 kbo-Isar-Amper rows labelled typo3_jobs still get the 109-posting group board, and why relabelling them to umantis would silently downgrade them to a 9-posting tenant. Ivan (2026-09-09): fine to separate kbo as its own bag, but gating groups before ats_type may be wrong -- experiment whether ats_type alone can express it (for example ats_type=kbo_group with careers_url pointing at the group board on every member row) so the routing algorithm stays one algorithm with no special pre-check. The experiment runs on the live clinics table copy, not on the live rows, and compares the resulting fetch plan and row counts for the 11 kbo rows and the 9 Barmherzige rows against today's.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A written comparison of fetch plan and postings for kbo and Barmherzige under both routings, on a registry copy
- [ ] #2 A recommendation whether group_portal_for can be deleted, with the exact registry rows that would change
<!-- AC:END -->
