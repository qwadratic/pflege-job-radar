---
id: TASK-47
title: Remove ats_type facet and coverage_pct/feature_score from clinics API and /pro
status: Done
assignee: []
created_date: '2026-09-11 05:01'
updated_date: '2026-09-22 18:59'
labels:
  - harvester
dependencies: []
ordinal: 47000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Judgment + TASK-25: ats_type carries no information for 242 of 407 clinics (5 labels resolve to one generic reader). coverage_pct conflates 'this hospital is hiring nothing' with 'the adapter is broken' -- measured 130 clinics on green boards with 0 open jobs read as a coverage gap today. Depends on TASK-25 (capability-based routing) and the harvest_report task above.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 clinics API drops coverage_pct and feature_score; a board's completeness verdict replaces both in /pro's coverage view
- [ ] #2 ats_type is relabelled 'family' pending TASK-25, not removed before then
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closed alongside TASK-25 (its hard dependency), for the same reason: ats_type removal / capability-based routing never happened and this session moved further in the opposite direction.
<!-- SECTION:FINAL_SUMMARY:END -->
