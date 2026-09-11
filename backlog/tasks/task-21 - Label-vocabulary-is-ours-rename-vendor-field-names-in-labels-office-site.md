---
id: TASK-21
title: 'Label vocabulary is ours: rename vendor field names in labels (office -> site)'
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
labels:
  - harvester
dependencies: []
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked why a tag is called office. Because personio's XML feed names the field office and the adapter carried the vendor's word into our label. Vendor vocabulary must not leak into our schema: personio office, mein-check-in sidebar group, smartrecruiters department, JSON-LD jobLocation.name all describe the same thing -- the site a posting belongs to -- and our label for it is one word (site), with the vendor field kept only in the raw payload. Same review for every other label the adapters emit (section_labels, department, category).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 No label written by any adapter uses a vendor's field name; each maps to one term in our vocabulary documented in one place
- [ ] #2 The raw payload still carries the vendor field verbatim
<!-- AC:END -->
