---
id: TASK-25
title: >-
  Remove clinics.ats_type; select helpers by probed board capability instead of
  a vendor label
status: To Do
assignee: []
created_date: '2026-09-09 22:20'
labels:
  - harvester
dependencies: []
ordinal: 25000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
ats_type is not a source of truth and never was. It is produced by our own regex list in crawlers/ats_discover2.py:45 (26 vendor patterns, first match wins, URL before HTML) and written back into clinics.ats_type through an inbox probe row. Measured 2026-09-09: 13 of 407 labels disagree with the live fingerprint; 17 labels resolve to only 12 distinct callables; five labels (typo3_jobs 57, self_hosted 46, wp_jobs 11, concludis 8, talention 6) plus the 114 empty ones all route to the same crawl_wp_jobs, so for 242 of 407 clinics the label carries no information at all. typo3_jobs names a CMS, self_hosted means we found no fingerprint, wp_jobs is a routing default rather than a fingerprint, bite_jobs duplicates bite. The label also caused a real outage: the smartrecruiters regex matched the widget script host static.smartrecruiters.com/job-widget/, so the adapter derived tenant job-widget and five clinics ingested cookie-banner text instead of 442 postings from 2026-09-06 until it was fixed.

Ivan's decision (2026-09-09): remove the field. The replacement is capability detection -- what the board actually offers decides the helper set: a JSON list endpoint, an XML or JSON feed, a sitemap that lists job URLs, server-rendered job links, or none of those. The vendor fingerprint may remain as one weak signal recorded in the harvest report, never as the routing key; what actually returned rows is the fact and is what the next run starts from.

Ordering matters: routing keys on ats_type today (crawlers/routing.py plan()), so the column cannot be dropped before capability detection exists, or every board falls through to the wp_jobs default. ats_type is referenced in 31 files and about 130 places, including the live API taxonomy facet, the /pro coverage table filter, app/data.py, app/coverage.py, app/targets.py, edge/pflege-ingest, sql/001_schema.sql and the skill docs; the web surfaces belong to another session and must be coordinated.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Helper selection is decided by probed board capability; no code path reads clinics.ats_type
- [ ] #2 The capability probe resolves at least as many boards to a working helper set as ats_type does today, measured board by board
- [ ] #3 The column is dropped from the registry, the schema, the ingest function, the API and the docs, and no facet or filter exposes it
- [ ] #4 The vendor fingerprint survives only as a recorded signal in the harvest report, never as a routing key
<!-- AC:END -->
