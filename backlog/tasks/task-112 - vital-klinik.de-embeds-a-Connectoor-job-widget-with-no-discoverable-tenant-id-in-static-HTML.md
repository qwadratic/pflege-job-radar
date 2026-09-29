---
id: TASK-112
title: >-
  vital-klinik.de embeds a 'Connectoor' job widget with no discoverable tenant
  id in static HTML
status: To Do
assignee: []
created_date: '2026-09-22 17:12'
updated_date: '2026-09-25 00:10'
labels:
  - crawler-coverage
dependencies: []
ordinal: 112000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinic 67170 (Vital Klinik Alzenau), board https://www.vital-klinik.de/ueber-uns/stellenanzeigen/. The page's static HTML has no job content at all (nav/header/footer only) -- a <script id="connectoorinit" src="https://fenster.connectoor.de/connectoor.js"> loads a third-party widget (api.connectoor.de) client-side. No data-tenant/company attribute sits on the script tag itself or anywhere else in the static page, unlike bite's data-bite-jobs-api-listing or the eventual TASK-97/TASK-99-adjacent vendor fingerprints already handled in this file -- the connectoor.js bundle itself was not decoded this session to find how it resolves which tenant/company to query for a given embedding domain. No existing adapter in this codebase handles Connectoor.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Decode connectoor.js (or capture its own network requests via Playwright) to find how the widget resolves its tenant for vital-klinik.de, and what the resulting job-list API call/response shape is
- [ ] #2 A new adapter (or Playwright reader) returns real postings for this board, verified live with a nonzero row count
- [ ] #3 Note whether Connectoor is worth a shared adapter (check the registry for other clinics using the same widget) or is a one-off
<!-- AC:END -->
