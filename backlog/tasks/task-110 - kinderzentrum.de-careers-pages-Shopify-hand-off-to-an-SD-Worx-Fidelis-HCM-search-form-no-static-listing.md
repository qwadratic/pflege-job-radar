---
id: TASK-110
title: >-
  kinderzentrum.de careers pages (Shopify) hand off to an SD Worx Fidelis HCM
  search form, no static listing
status: To Do
assignee: []
created_date: '2026-09-22 17:12'
updated_date: '2026-09-25 00:10'
labels:
  - crawler-coverage
dependencies: []
ordinal: 110000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinics 17105/17106 (Zentrum für Kinder und Jugendliche Inn-Salzach e.V., Altötting), boards https://kinderzentrum.de/pages/karriere and /pages/stellenangebote. Both are Shopify pages (Shopify's own cart/checkout template renders alongside the content, confirms the platform) whose only real job-application link is an SD Worx Fidelis HCM 'Initiativbewerbung' (unsolicited application) URL: https://klm-hcm.fidelis.sdworx.de/fidelishcm/jobexchange/applyBy.do?j=&jobOfferId=8ac789a69e226576019e255f855910d7. Following the same host to https://klm-hcm.fidelis.sdworx.de/fidelishcm/jobexchange (redirects to showJobOfferList.do?init=true) shows a real search FORM, not results -- 'Berufsfelder' (incl. Pflegedienst) and 'Konzernstrukturen' filter checkboxes for a whole hospital group (InnKlinikum Altötting und Mühldorf, InnCare und Service GmbH, Kliniken Mühldorf a. Inn Service GmbH, MVZ, and Zentrum für Kinder und Jugendliche Inn-Salzach e.V. itself). The actual results load via a GWT-RPC POST (publicRPC/WebPositionGwtService), not a plain GET with query params -- not reverse-engineered this session. No existing adapter in this codebase handles SD Worx/Fidelis HCM.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The GWT-RPC request shape WebPositionGwtService needs to return a result scoped to 'Zentrum für Kinder und Jugendliche Inn-Salzach e.V.' is reverse-engineered (via browser devtools network tab or Playwright request interception) or a plain Playwright fill-and-submit of the search form is used instead
- [ ] #2 A new adapter (or Playwright-driven reader) returns real postings for this tenant, verified live with a nonzero row count and at least one field-complete row
- [ ] #3 Diagnosis notes whether other Bavarian clinics in the registry belong to the same InnKlinikum/SD Worx group (worth a shared adapter) or whether this is a one-off
<!-- AC:END -->
