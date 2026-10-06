---
id: TASK-431.1
title: >-
  Audit of the static clinic fields: source, fill rate, correctness and use of
  every field (read-only report)
status: To Do
assignee: []
created_date: '2026-10-06 07:20'
labels:
  - registry
  - data-quality
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 302000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
For every static field of a registry clinic (name, operator, traegerart, status, beds, day_places, size, versorgungsstufe, fachrichtungen, landkreis, regierungsbezirk, town, plz, lat/lon/geo_source, careers_url, ats_type, source, employer_id, website, photo, blurb): where does the value come from (Krankenhausplan PDF, Reha list, manual, crawl), how many of 651 are filled, a sample check of correctness against the source, and who reads it (code, API, UI, tests). plz is filled 0/651 and employer_id 0/651 today: say why. Result is a table in the task notes plus a list of fields that nobody reads or that are wrong. Also answer: per clinic, how complete is what we know (registry fields, vacancy fields we can extract from its board, membership of a group or operator) and how clean its postings are; propose one measure of that, without building it. No data changes in this task.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Table of all static clinic fields with source, fill rate of 651, sampled correctness and readers
- [ ] #2 List of unused or wrong fields with a recommendation (keep, fix, drop) per field
- [ ] #3 A proposal of one per-clinic completeness measure, with the inputs it needs
<!-- AC:END -->
