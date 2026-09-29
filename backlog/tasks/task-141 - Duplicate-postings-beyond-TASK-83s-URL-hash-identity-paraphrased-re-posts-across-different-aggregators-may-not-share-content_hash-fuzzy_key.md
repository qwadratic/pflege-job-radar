---
id: TASK-141
title: >-
  Duplicate postings beyond TASK-83's URL/hash identity: paraphrased re-posts
  across different aggregators may not share content_hash/fuzzy_key
status: To Do
assignee: []
created_date: '2026-09-23 16:34'
updated_date: '2026-09-25 00:10'
labels:
  - db-quality
dependencies: []
priority: low
type: task
ordinal: 141000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-83 dedupes on canonical vendor job id / content_hash / fuzzy_key (title+employer+city+description-prefix). That catches the same posting re-observed under different URL shapes from the SAME source, but not a genuinely paraphrased re-post of the same real opening across two DIFFERENT aggregators (e.g. an agency board and the employer's own site, worded differently) -- title/description text differs enough that content_hash/fuzzy_key miss it, but a human would recognize it as the same job. Not yet measured how common this actually is in the live data; could be negligible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Measure live: sample same-clinic, same-published-week postings from different source_ids, check for high title/description similarity (fuzzy string match, not exact) not already caught by fuzzy_key
- [ ] #2 Report a real duplicate-rate number before deciding whether a fix (a looser cross-source similarity merge) is worth building
<!-- AC:END -->
