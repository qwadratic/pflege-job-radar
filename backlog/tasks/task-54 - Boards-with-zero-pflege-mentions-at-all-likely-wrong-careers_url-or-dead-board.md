---
id: TASK-54
title: >-
  Boards with zero 'pflege' mentions at all -- likely wrong careers_url or dead
  board
status: To Do
assignee: []
created_date: '2026-09-11 10:50'
labels: []
dependencies: []
ordinal: 54000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-11 recon: 4 zero-yield boards returned HTTP 200 but the fetched page contains no occurrence of the word 'pflege' anywhere, unlike every other clinic career page in the registry. This is a stronger signal than a JS-widget gap -- it suggests the careers_url in the registry no longer points at a real careers/jobs page at all (redirected to a generic landing page, retired board, or wrong domain entirely). ukr.concludis.de is the highest-value one at 839 beds (Uniklinik Regensburg).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 ukr.concludis.de (839 beds): confirm current real career URL for Uniklinik Regensburg and fix the registry, or confirm concludis widget genuinely has no static fallback and needs the concludis-specific approach already used elsewhere
- [ ] #2 www.artemedmuenchen.de, www.vital-klinik.de, www.310klinik.com: each checked for a live, correct careers page; registry updated or the board flagged dead
- [ ] #3 No fix applied blind -- each of the 4 confirmed by opening the actual current site before changing anything, since 'no pflege keyword' could also mean the clinic genuinely has zero pflege-department content by design
<!-- AC:END -->
