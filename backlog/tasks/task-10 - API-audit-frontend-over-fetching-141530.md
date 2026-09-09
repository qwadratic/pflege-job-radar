---
id: TASK-10
title: 'API audit: frontend over-fetching (#14,15,30)'
status: To Do
assignee: []
created_date: '2026-09-08 23:18'
labels:
  - frontend
dependencies: []
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Full findings: harness/evals/api-layer-audit-2026-09-08/findings.md. Opening #/clawl fires 12 requests, 3 literal duplicates (/api/settings fetched 3x, /api/hunter/status 2x); the Firecrawl balance arrives via 3 different endpoints at 3 refresh rates and can show 3 different numbers on one screen. clinicOptions() pulls the full ~400-row clinic table (with career_profile/fachrichtungen blobs) to build a 2-string dropdown option, and pageScrape blocks first paint on it. billToday() fetches the whole billing report just for one header float, on every kostenLink() render. Boot chain is strictly sequential (4 round trips) where Promise.all would do.
<!-- SECTION:DESCRIPTION:END -->
