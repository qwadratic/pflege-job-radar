---
id: TASK-187
title: >-
  Sitemap walk in find_job_urls stops at max_maps=500 files without recording
  truncated
status: To Do
assignee: []
created_date: '2026-10-01 18:01'
labels:
  - crawler
  - db-quality
dependencies: []
priority: low
ordinal: 184000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-01 by the TASK-184 Altenpflege research (read in code by hand). crawlers/vendor_adapters.py:630 find_job_urls(base, session=None, max_maps=500) walks robots.txt + sitemap indexes with 'while queue and n < max_maps'; the comment calls 500 a 'loop-safety ceiling'. TASK-14 (Done) made such ceilings loop-safety defaults that RECORD truncated (crawl_issues kind='truncated'); this walk has no such record: when n reaches 500 the rest of the sitemap tree is dropped and the board reads as complete. The loop already has a 'seen' set, so only an endless stream of fresh sitemap URLs could need the ceiling. Same pattern with silent narrowing in crawlers/ats_discover2.py:105 sitemap_urls(max_maps=6) plus 'queue += [...][:3] or locs[:2]' (discovery angle, not the vacancy walk). CLAUDE.md: the only stop conditions are the source's own end signal or a budget recorded as truncated, never as success.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 find_job_urls records truncated (value and count reached) in the same place as the TASK-14 walks when n reaches max_maps, so the board's run reads as truncated, not complete; test with a fake sitemap tree larger than the ceiling, mutation-checked
- [ ] #2 ats_discover2.sitemap_urls: the max_maps=6 stop and the [:3] / locs[:2] child narrowing either go away (end signal = empty queue) or are recorded as truncated; Ivan decides which, nothing is invented
<!-- AC:END -->
