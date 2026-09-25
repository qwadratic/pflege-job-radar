---
id: TASK-109
title: >-
  kh-nuernberger-land.de's only real board is the WHOLE klinikum-nuernberg.de
  group portal, not scoped to Lauf/Altdorf
status: To Do
assignee: []
created_date: '2026-09-22 17:11'
updated_date: '2026-09-25 00:10'
labels:
  - crawler-coverage
dependencies: []
ordinal: 109000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinics 57401 (Krankenhaus Lauf a.d. Pegnitz) and 57403 (Krankenhaus Altdorf b. Nürnberg): the registered careers_url (https://www.kh-nuernberger-land.de/ueber-uns/wir-stellen-uns-vor/) links 'Stellenangebote' to https://karriere.klinikum-nuernberg.de/ueber-uns/unsere-standorte-und-ihre-besonderheiten-/-anfahrt/krankenhaeuser-nuernberger-land/ -- a page ABOUT the Nürnberger Land sites, not a filtered board. Verified live: crawl_wp_jobs on that URL returns 122 rows spanning specialties (Frauenheilkunde, Kinderchirurgie, Mund-Kiefer-Gesichtschirurgie, ...) that Lauf/Altdorf (small district hospitals) almost certainly do not have -- several rows explicitly self-tag 'Standort: Klinikum Nürnberg | Campus Süd', a different site entirely. This is the shared-board-attribution problem TASK-81/TASK-57/TASK-68 already document for other group portals: a naive careers_url registry write here would attach the ENTIRE Nürnberg city hospital system's postings to 2 small district hospitals, over-attribution, not a genuine fix. Needs a real per-posting location/department match against Lauf and Altdorf specifically (the board's own postings do carry a Standort/location field, per the 3 rows observed with one), not a blind registry URL change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Diagnosis confirmed: does the karriere.klinikum-nuernberg.de board expose a location or site facet/filter/query param that actually narrows to just Krankenhaus Lauf and Krankenhaus Altdorf, distinct from the rest of the Klinikum Nürnberg group
- [ ] #2 If a real per-site filter exists, careers_url is set to the filtered form and verified live to return only Lauf/Altdorf postings, not the full group
- [ ] #3 If no server-side filter exists, a client-side location match (using the board's own Standort/location field on each row) is implemented before these rows are attributed to clinic 57401/57403, and verified live: rows attributed to these two clinics all carry a Lauf or Altdorf location
- [ ] #4 No registry write attaches the unfiltered 122-row group board to clinic 57401/57403
<!-- AC:END -->
