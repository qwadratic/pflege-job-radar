---
id: TASK-447
title: >-
  A clinic's careers_url without a scheme (a staging host) makes the wp_jobs
  adapter build base '://' and warn on every run
status: To Do
assignee: []
created_date: '2026-10-07 10:21'
labels:
  - crawler-coverage
  - registry
dependencies: []
priority: low
ordinal: 327000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Seen by pflege-fe in the worker log of runs 240 and 241 (2026-10-07): '[wp_jobs] find_job_urls: no job links in sitemap for :// (0 sitemap urls seen)', repeated. Cause found by pflege-clawl: crawl_wp_jobs builds base as scheme://netloc from the clinic's careers_url; one clinic row (RH2736, a Munich geriatric rehabilitation site of a Red Cross operator) has a careers_url with no scheme that points at a vendor's staging host (kunden-projekt.dev), so urlparse gives an empty netloc and base becomes '://'. It is the only one of 651 clinics without a host. The warning is loud, as it should be; the defect is the registry value. Fix: find the clinic's real careers page by hand (website, Impressum, a first-hand page), write it with tools/apply_clinic_corrections.py (reason code board_location, evidence the page), and decide with the adapter test whether a careers_url without a scheme should fail loudly at registry build instead of at crawl time (no silent fallback). Not done: any change to the clinic row; the board does not carry real postings today (0 rows in runs 237 and 240).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 RH2736 carries the clinic's own careers page (with scheme) or an explicit reason why it has none, written with the corrections tool and its evidence
- [ ] #2 A careers_url without a scheme is rejected where the registry value is written or built, with a test, so the crawl never sees base '://'
<!-- AC:END -->
