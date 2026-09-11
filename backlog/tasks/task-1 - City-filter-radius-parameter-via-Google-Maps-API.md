---
id: TASK-1
title: 'City filter: radius parameter (Destatis coordinates, NOT Google Maps)'
status: To Do
assignee: []
created_date: '2026-09-08 22:50'
updated_date: '2026-09-09 07:26'
labels:
  - frontend
dependencies: []
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
User cross-checked klinikradar.de (patient hospital directory, not a jobs board -- has location+specialty search, city dropdown). Add a radius param to our city facet filter: resolve a city to lat/lon, filter clinics within radius.

CORRECTION 2026-09-09, verified: Google Maps Geocoding is NOT usable here. Maps Service Terms 6.3.1 permits caching lat/lng for at most 30 consecutive days; 6.3.2 permits indefinite caching only for the single end user who triggered the request and forbids reuse across end users. A shared coordinates column that filters for every visitor is exactly what that forbids, and 6.2 forbids displaying the content on a non-Google map (rules out Leaflet). Cost was never the blocker -- licence is.

Use instead the Destatis 'Auszug aus dem Gemeindeverzeichnis' xlsx: https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/Gemeindeverzeichnis/Administrativ/Archiv/GVAuszugQ/AuszugGV1QAktuell.xlsx -- 1.8 MB, 10943 German municipalities, 10940 carry official centre-point coordinates, plus PLZ and the AGS whose first two digits give the Bundesland (09 = Bayern). Licence per the archive's Hinweise.txt: 'Vervielfaeltigung und Verbreitung, auch auszugsweise, mit Quellenangabe gestattet' -- permanent storage and redistribution allowed with attribution. Quarterly refresh. The same file also supplies the Land labelling data, so this task shares a dataset with the geo-labelling work and should not ship a second source of truth for coordinates.

For dirty free-text strings that are not clean town names (e.g. 'Klinik Hohe Mark Oberursel'), the only working fallback is an OSM POI geocoder (Photon returns state + coordinates and exposes ambiguity honestly); postal registries cannot resolve business names.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Radius filter resolves a city to coordinates from the shipped Destatis dataset, with no runtime third-party geocoding call
- [ ] #2 No Google Maps API call exists anywhere in the coordinate path
- [ ] #3 Coordinates are shared by the Land-labelling work rather than duplicated in a second dataset
<!-- AC:END -->
