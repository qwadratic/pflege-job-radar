---
id: TASK-52
title: >-
  meinkrankenhaus2030.de: title extraction returns generic 'Stellenanzeige'
  instead of real job title
status: To Do
assignee: []
created_date: '2026-09-11 10:49'
labels: []
dependencies: []
ordinal: 52000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Krankenhaus Weilheim (clinic_id 19002, 220 beds) is hosted on a HubSpot-templated career site (meinkrankenhaus2030.de, ?hsLang=de-de query params). Its careers_url was fixed 2026-09-11 from a stale single-job link to the real listing (/karriere/stellenboerse), which confirmed the listing has 17 real postings including at least one certified-nursing role ('Gesundheits- und Krankenpfleger/-Operations-Technischen Assistent'). But crawl_wp_jobs's generic title extraction returns the literal string 'Stellenanzeige' (German for 'job posting') for every single row instead of the per-posting title, so classify_role() correctly rejects all 17 as non-nursing (no real title to match against) and 0 rows are kept. This is a template-specific title-selector gap, not a routing or URL problem.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Real per-posting title extracted from meinkrankenhaus2030.de detail pages (check for HubSpot-specific meta/JSON-LD/heading selector this template uses instead of the generic <title> or <h1> crawl_wp_jobs currently falls back to)
- [ ] #2 Krankenhaus Weilheim's real nursing postings appear in prod after the fix
- [ ] #3 Fix scoped to not regress other wp_jobs-routed HubSpot or non-HubSpot boards (existing test suite in tests/test_completeness_wp_jobs.py stays green)
<!-- AC:END -->
