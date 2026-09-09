---
id: TASK-3
title: 'Clawl dashboard: clarify Hunter vs Schedules vs one-off Clawl, reduce overlap'
status: To Do
assignee: []
created_date: '2026-09-08 22:50'
labels:
  - frontend
dependencies: []
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Opinion from the session: the Clawl page crams 3 different 'get more job data' mechanisms (Hunter daemon / continuous, Schedules / cron autocrawl, New Clawl / one-off targeted run) with no explanation of when to use which -- feels 'namesheno' (messy/mixed together). Consider: a one-line explainer per mechanism, and/or actually merging Schedules into the Hunter+campaign model since the resilient hunter daemon (app/hunter.py) + the new reingest-campaign routine (docs/campaign.md) already cover continuous coverage better than plain cron. Needs a product decision, not just UI polish -- scope it before building.
<!-- SECTION:DESCRIPTION:END -->
