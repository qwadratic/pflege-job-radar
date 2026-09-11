---
id: TASK-24
title: >-
  Mirror correctness: the offline copy reproduces the live page-transition graph
  exactly, for every mirror
status: To Do
assignee: []
created_date: '2026-09-09 12:21'
labels:
  - harvester
dependencies: []
ordinal: 24000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's rule for the browsable mirror (2026-09-09): what matters is that the graph of transitions between pages is the same offline as online; how URLs look is irrelevant. So the procedure is: enter the site by hand (the crawler walks it), save everything, then relink the saved pages into a graph identical to the live one. This applies to every mirror without exception, including API-driven boards where the click surface is a generated index. Measured failure today (docs/reviews/raw-first-review.html section 13, F3): on 2 of 3 html boards the detail-to-listing back-link stays absolute because the site links /stellenportal, the server answers 301 to /stellenportal/, and wget's --convert-links never rewrites the redirect source; rexx pages also link http:// with a trailing ? that is not converted. The manifest must therefore record the link graph (from-page, to-page) observed live, and a post-processing step must rewrite every href so the same graph exists on disk; the acceptance check is graph equality, not URL equality.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each snapshot's manifest records the live link graph as edges between fetched pages
- [ ] #2 After relinking, every edge in the live graph resolves to an existing local file, and the check runs automatically on every mirror
- [ ] #3 The check fails the load with the list of missing edges when the graphs differ
<!-- AC:END -->
