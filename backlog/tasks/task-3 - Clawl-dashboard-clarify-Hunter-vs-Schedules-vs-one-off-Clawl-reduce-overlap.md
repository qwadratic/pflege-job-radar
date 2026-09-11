---
id: TASK-3
title: 'Clawl dashboard: clarify Hunter vs Schedules vs one-off Clawl, reduce overlap'
status: In Progress
assignee: []
created_date: '2026-09-08 22:50'
updated_date: '2026-09-09 01:24'
labels:
  - frontend
dependencies: []
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Opinion from the session: the Clawl page crams 3 different 'get more job data' mechanisms (Hunter daemon / continuous, Schedules / cron autocrawl, New Clawl / one-off targeted run) with no explanation of when to use which -- feels 'namesheno' (messy/mixed together). Consider: a one-line explainer per mechanism, and/or actually merging Schedules into the Hunter+campaign model since the resilient hunter daemon (app/hunter.py) + the new reingest-campaign routine (docs/campaign.md) already cover continuous coverage better than plain cron. Needs a product decision, not just UI polish -- scope it before building.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Layout decided with the user (variant A2, mockups in docs/mockups/clawl/): a sub-header of tabs Run/Coverage/Automation/History, with the Run tab in two columns -- run form plus recent runs on the left, coverage and automation summaries on the right. Nothing was removed: Hunter, Schedules and Inbox now sit together under Automation, coverage keeps its full table under Coverage, the full run list under History. Implemented in web/pro.template.html (pageScrape) and rebuilt into web/pro.html; covered by tests/test_web_clawl.py. Still open from the same session: per-run failure triage with fix buttons, wiring GET /api/crawl/estimate into the run form, live-run panel with cancel, one-click adapter-vs-Firecrawl compare, run-this-ATS-vendor from a coverage row, and the mobile layout.
<!-- SECTION:NOTES:END -->
