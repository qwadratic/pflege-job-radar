---
id: TASK-4
title: 'Coverage table: default-sort by gap, exclude bot-walled'
status: To Do
assignee: []
created_date: '2026-09-08 22:50'
labels:
  - frontend
dependencies: []
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
User wants a quick answer to 'which clinics/ATS have the biggest crawl gap right now, excluding portals we know are bot-walled'. GET /api/coverage already has per-adapter coverage_pct and a walled count; make the Clawl 'Coverage by adapter' table sort ascending by coverage_pct by default (worst gap first) and add a toggle/column to exclude or flag bot-walled clinics, so the worst-gap adapters are the first thing visible instead of requiring manual sorting/thinking.
<!-- SECTION:DESCRIPTION:END -->
