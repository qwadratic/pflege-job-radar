---
id: TASK-7
title: 'API audit: docs/skill reference drift (#8,9,22-24,33)'
status: To Do
assignee: []
created_date: '2026-09-08 23:18'
labels:
  - api
dependencies: []
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Full findings: harness/evals/api-layer-audit-2026-09-08/findings.md. docs/api.md, docs/auth.md, skill/references/api.md all have wrong auth claims (says 'no auth for reads' while 8 prefixes are owner-gated) and are missing ~69 routes vs /api/openapi.json (including the whole hunter/scheduler/inbox/billing operational surface and now /api/autopilot). List envelope claim ({total,rows}) only applies to some endpoints, others return bare arrays. Field-level drift on 8+ endpoints (settings, clinics, facets, search, stats, mechanics, crawl, crawl/plan). Regenerate from live responses + app/auth.py's actual prefix lists, then rebuild web/skill/ (python web/build.py).
<!-- SECTION:DESCRIPTION:END -->
