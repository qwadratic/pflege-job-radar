---
id: TASK-2
title: 'Clawl dashboard: low-credit / PAYG-reload-pending banner'
status: Done
assignee: []
created_date: '2026-09-08 22:50'
updated_date: '2026-09-08 22:52'
labels:
  - frontend
dependencies: []
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-08: Firecrawl balance sat near/at the reserve floor with no visible signal on the dashboard; a district run then burned 4 doomed attempts (backend fixed in app/crawl.py MIN_VIABLE_CAP, commit 800eedd) before anyone noticed the account was just low on credits. Add a visible banner/badge on the Clawl page (and maybe the header credits pill) when GET /api/firecrawl/credits.remaining is below a threshold (e.g. reserve_credits), so a thin budget is obvious before someone fires a run into it.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented: header pill .warn class + tooltip below 50 remaining credits. Commit 3e482d5.
<!-- SECTION:NOTES:END -->
