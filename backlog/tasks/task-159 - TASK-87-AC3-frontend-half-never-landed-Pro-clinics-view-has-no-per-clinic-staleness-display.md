---
id: TASK-159
title: >-
  TASK-87 AC#3 frontend half never landed: Pro clinics view has no per-clinic
  staleness display
status: To Do
assignee: []
created_date: '2026-09-24 23:43'
updated_date: '2026-09-25 00:10'
labels:
  - frontend
dependencies: []
ordinal: 159000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
GET /api/coverage already carries clinic_freshness (per clinic with >=1 open posting: clinic_id, name, open_jobs, last_seen, stale_days) since TASK-87 (closed 2026-09-23, API half of AC#3). The other named surface, web/pro.template.html's clinics view, was explicitly left unwired (no Python/API file owns that render, out of TASK-87's file scope) and confirmed today (2026-09-24) via grep still not wired -- clinic_freshness never appears in web/pro.template.html. A clinic can still visually read 'complete' in the Pro UI on 16+ day old rows, the exact problem TASK-87 was filed to fix; only the API half exists.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Pro clinics view renders each clinic's stale_days (or last_seen date) from GET /api/coverage's clinic_freshness, worst-first or sortable, no invented staleness threshold/color-coding beyond what's asked
- [ ] #2 Verified live in a browser: a known-stale clinic (re-check current staleness via clinic_freshness live, don't assume the 2026-09-21 audit's named clinics are still stale) visibly shows its age in the Pro UI
<!-- AC:END -->
