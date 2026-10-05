---
id: TASK-200
title: >-
  Clinic rows carry lat, lon, geo_source, geo_name so a map can place all 651
  sites (public API)
status: Done
assignee: []
created_date: '2026-10-02 08:59'
updated_date: '2026-10-05 15:40'
labels:
  - api
  - geo
dependencies: []
priority: medium
ordinal: 197000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan (2026-10-02, via the career agent session): the n8n demo of Bavarian clinics on a map found coordinates for 75 of 651 clinics only, because lat/lon came from /api/jobs (postings). The registry has no coordinates. pflege_jobs.geo.clinic_centroid gives the centre of the clinic's Bavarian municipality (data/geo/gemeinden_de.csv, Destatis; override file data/geo/clinic_town_overrides.json for 13 registry spellings and districts); app/data.py puts lat, lon, geo_source ('municipality_centroid') and geo_name on every clinic row, so GET /api/clinics (public) carries them. It is the centre of the town, no address: clinics of one town share a point (München 58). Branch worktree-clinic-geo, commit 6622fd9, not yet on main; goes live with the restart after the nightly crawl.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 GET /api/clinics returns lat, lon, geo_source and geo_name for every registry clinic (651 of 651 on 2026-10-02), checked on the live service after the restart
- [x] #2 tests/test_clinic_geo.py names any registry town the geo table cannot place; the town list fixture is refreshed when the registry gains towns
- [x] #3 Address-level coordinates (Impressum / Krankenhausplan address geocoding) are decided separately: this task ships the municipality centre only
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Re-check 2026-10-05 (pflege-clawl). Live GET /api/clinics, read page by page until next_offset is null, total 651: lat+lon on 651, geo_source on 651, geo_name on 651 (service restarted 2026-10-05 11:50 UTC with commit 74c23b1). tests/test_clinic_geo.py: 11 passed (every one of the 287 registry towns places exactly one point inside Bavaria; an unplaceable town fails by name). docs/api.md, Clinic row, states that the point is the centre of the municipality, no address, and that clinics of one town share a point. Address-level geocoding is not part of this task (AC 3).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Every clinic row of GET /api/clinics carries lat, lon, geo_source and geo_name (651 of 651 live, municipality centre from Destatis). Verified on the live service and with tests/test_clinic_geo.py (11 passed). Address-level coordinates are a separate decision.
<!-- SECTION:FINAL_SUMMARY:END -->
