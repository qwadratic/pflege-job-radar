---
id: TASK-42
title: 'Judgment after completeness: API expressiveness and /pro panel noise'
status: To Do
assignee: []
created_date: '2026-09-10 07:49'
labels:
  - harvester
dependencies: []
ordinal: 42000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked (2026-09-10) whether the completeness work leads to order, a more concise and expressive API, a less noisy /pro control panel and a more complete public site. Once the adapters are green, review the API surface and the /pro panel against what the completeness data now makes unnecessary (ats_type facet, adapter-mode switches, per-vendor toggles) and propose removals, with the frontend session for web/* changes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A written list of API endpoints and /pro controls that the completeness data makes redundant, each with the evidence
- [ ] #2 Removals are proposed as backlog tasks with the frontend label where they touch web/*
- [ ] #3 The public site shows every complete board's postings after the next full load
<!-- AC:END -->
