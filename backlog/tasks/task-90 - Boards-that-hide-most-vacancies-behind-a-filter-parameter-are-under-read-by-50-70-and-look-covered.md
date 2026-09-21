---
id: TASK-90
title: >-
  Boards that hide most vacancies behind a filter parameter are under-read by
  50-70% and look covered
status: To Do
assignee: []
created_date: '2026-09-21 04:27'
labels: []
dependencies: []
ordinal: 90000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, finding M10. Not JS, not bot-walled -- plain HTTP, but the default view is a lie.

- Klinikum Kaufbeuren (76201): the default board page returns 11 links with ZERO nursing. The 16 Pflege rows only exist under ?selection3=3&page=N.
- Krankenhaus Barmherzige Brüder München (16214): serves 10 of 21 with no page links and an un-clickable pager; the 11 Pflege rows only appear under ?tx_oycimport_list[category]=15.
- Barmherzige Schwandorf: the board silently defaults to a single-location view, 30 rows against 129 with ?...[location]=all.

A crawler that fetches careers_url and walks anchors under-reports these by 50-70% while reporting success. This is the same class of failure as TASK-85 (silent near-zero yield) but with a different trigger: the board answers 200 with real job links, just not most of them.

Generalisation worth making rather than three one-off URL fixes: a board whose listing carries filter/pagination parameters should be walked across its parameter space, and the board's own total (TASK-88) is what proves the walk was complete.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The three named boards yield their full nursing counts: 76201 (16 Pflege), 16214 (11 Pflege), Barmherzige Schwandorf (129 rows with location=all)
- [ ] #2 Filter/pagination parameter walking is handled generically where the board exposes its parameter space, not as three hardcoded URLs
- [ ] #3 Each of the three is cross-checked against the board's self-reported total per TASK-88, so completeness is proven rather than assumed
<!-- AC:END -->
