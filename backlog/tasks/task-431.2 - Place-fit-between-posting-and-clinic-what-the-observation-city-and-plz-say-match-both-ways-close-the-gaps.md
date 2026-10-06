---
id: TASK-431.2
title: >-
  Place fit between posting and clinic: what the observation city and plz say,
  match both ways, close the gaps
status: To Do
assignee: []
created_date: '2026-10-06 07:20'
labels:
  - registry
  - data-quality
  - geo
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 303000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Posting observations carry city and plz (5970 rows, 755 distinct pairs, 572 cities); the registry has 651 clinics in 287 towns and no plz. 354 of the 570 posting cities match no clinic town by plain string, 71 clinic towns have no posting city. Classify the 354: spelling variant, district or ward of a clinic town, branch of an operator in another town, place outside Bavaria, wrong value from the parser. Then make the place link work in both directions: from a posting city or plz to the clinic or clinics there (and to the operator), and from a clinic to every place its postings name. Fix what is wrong at the source (parser or adapter, not by hand per row). Fill the clinic plz if a reliable source exists. Uses pflege_jobs/geo.py and data/geo/gemeinden_de.csv; the mirror snapshot tests/fixtures/mirror_infra/infra__registry-read-proxy.sqlite.xz holds the pairs and can be reduced to the 755 distinct pairs afterwards.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The 354 unmatched posting cities are classified with counts per class
- [ ] #2 A documented function or view maps a posting place to clinics and a clinic to its posting places, tested on the mirror pairs
- [ ] #3 Fixes for wrong values land at the source with red tests first; DB writes only after Ivan approves the exact counts
<!-- AC:END -->
