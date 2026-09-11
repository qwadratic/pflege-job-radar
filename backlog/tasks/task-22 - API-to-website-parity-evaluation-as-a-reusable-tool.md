---
id: TASK-22
title: API-to-website parity evaluation as a reusable tool
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
updated_date: '2026-09-09 11:42'
labels:
  - harvester
dependencies: []
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's rule (2026-09-09): reading an API instead of the rendered page is a change of source and must be proven equivalent, sampled, in both directions. Procedure: query the API in at least two ways (paginated small pages, one large page, a filtered query) and check the id sets agree; draw a random sample of postings from the API, construct or find each one's public page, and compare title, description text and HTML structure, location, dates, employment type and apply link; then the reverse, which he stressed most -- enumerate everything the site shows (all listing pages, sections, filters) and confirm nothing on the site is absent from the API. Families using an API today: smartrecruiters, dvinci, personio, oracle (jobs.feed.json), bite, softgarden.

First pass ran on 2026-09-09 (workflow wj9kvoj0z; full spec, per-family API query ways, field normalisation rules, pass/fail thresholds and the tool sketch are in /tmp/wf5/spec.md and are mirrored in docs/reviews/raw-first-review.html section 17). Results: softgarden, oracle, bite and dvinci are one-to-one on ids; personio's XML lacks the page's city and republish date and leaks the vendor office name as city; dvinci's parse_dvinci reads startDate (null on 7 of 8) where the page's datePosted is the API's jobOpening.createdDate; smartrecruiters' adapter never reaches the API at all because the tenant-id regex matches the widget script URL job-widget instead of the company_code ArtemedSE, and parse_smartrecruiters stores the API self-link ref as the posting URL. This task turns the spec into tools/api_web_parity.py: one function per family yielding (api_items, site_items, url_for), a generic core, and a harvest_report row with parity_sample_n, parity_identical, parity_differs, site_only split live/expired, api_only, completeness_signal, verdict, evidence, ways_ignored_params, seed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 tools/api_web_parity.py runs for every API family and writes a parity row to harvest_report
- [ ] #2 Any posting present on the site but absent from the API fails the family's verdict and lists the URL
- [ ] #3 The tool runs on first adoption of an API for a family and on every adapter change to that family
<!-- AC:END -->
