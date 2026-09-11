---
id: TASK-14
title: >-
  Remove self-imposed crawl caps; replace with a completeness verdict and a
  truncated flag
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
updated_date: '2026-09-09 12:21'
labels:
  - harvester
dependencies: []
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Every crawler carries a page or item ceiling that nobody decided as a product rule: GROUP_PORTALS pages=12 (crawlers/vendor_adapters.py:901), Crawler per_site_pages=120 and list_pages=12 (pflege_jobs/sources/career_crawl.py:104), _fallback_jobposting_links limit=150 (pflege_jobs/sources/bite.py:247), VENDOR_MAX_JOBS default 300 (crawlers/vendor_adapters.py:545), SmartRecruiters ceiling 1000. git blame shows all of them were introduced by AI sessions on 2026-09-06, 09-07 and 09-09 as safety defaults while writing the adapters, not by Ivan. Ivan's rule (2026-09-09): he is against our own limits on content. A board that hits one of these today is reported as a normal successful crawl, so a 130-job board silently loses 10 jobs and nobody sees it. Distinction Ivan drew: a cap that prevents pointless spend of a paid or expensive step (Firecrawl credits, Playwright minutes) is legitimate -- it bounds one step, the steps then run in sequence and the configuration is changed in flight, small step, fix, small step. That kind of cap stays and is recorded. A cap on how much of a free board we read is not legitimate and goes. The only stop conditions for reading a board are the site's own end of pagination or a recorded budget stop written as truncated, never as completion.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 No adapter stops early on a hardcoded page or item count; pagination runs until the site's own end signal
- [ ] #2 Any remaining budget stop (bytes, wall time, host politeness) writes result=truncated with the count reached, never ok
- [ ] #3 kbo.de group board returns all 109 postings without a pages= constant in the code
<!-- AC:END -->
