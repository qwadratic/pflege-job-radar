---
id: TASK-182
title: >-
  5 registry sites routed to pi_asp have no P&I seed and fail every crawl:
  18004, RH1600, RH1501, 27706, RH1745
status: To Do
assignee: []
created_date: '2026-09-29 23:15'
labels:
  - adapter
dependencies: []
ordinal: 179000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-29 (TASK-178 P&I work): these clinics carry ats_type pi_asp but data/registry/pi_seeds.json has no seed for them, so every nightly crawl logs 'no P&I seed for this clinic' and reads 0 rows -- the same failure 16107 ZPG had. Each needs its real board found (host + companyEid/param), verified live against the board's own row list, and seeded -- or its ats_type corrected if it is not P&I.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each of the 5 sites either reads its board completely (board rows = adapter rows, live dry run) or has a corrected careers_url/ats_type recorded with code board_location
<!-- AC:END -->
