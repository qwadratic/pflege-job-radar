---
id: TASK-84
title: >-
  Non-vacancy pages stored as open nursing postings: ~120 junk rows from loose
  JOB_PATH, loose link emission and a catch-all role class
status: To Do
assignee: []
created_date: '2026-09-21 04:26'
labels: []
dependencies: []
ordinal: 84000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21: about 16 clinics, 120 rows that are not vacancies at all -- news articles, category index pages, marketing landing pages, a PDF asset and a medical-glossary entry. Three compounding causes:

1. crawlers/vendor_adapters.py:466 JOB_PATH matches any URL containing /jobs/, so 117 of the 175 rows the München Klinik board returns are content pages. Its /(karriere-)?detail/[^/?#] alternative matches any /detail/ path, which is how klinikum-msp.de's glossary entry /patienten-besucher/glossar/detail/fusspflege is stored as an open nursing posting.

2. pflege_jobs/sources/career_crawl.py:334-335 emits a link as a posting whenever JOB_HREF matches, the anchor text is longer than 6 characters and LIST_NAV.fullmatch() does not fire. LIST_NAV lists the bare word 'pflege', so 'Pflegedienst' and 'Ansprechpartner' both pass. This costs in BOTH directions: 12 junk rows at 56101, and at 36202 the category link is emitted as a posting and therefore never enqueued as a list page, so the BFS never reaches /alle-stellenangebote and 9 of 14 real vacancies are never seen.

3. classify_role falls back to ('sonstige_pflege','fallback') for any title containing a Pflege token, and sonstige_pflege is NOT in patterns.json:344 excluded_role_classes. Verified: classify_role('PFLEGEN KÖNNEN.') returns ('sonstige_pflege','fallback'). That is how 31 Memmingen news headlines became open postings.

Net effect measured at Memmingen: shows 40 postings, holds 9 real ones.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A link is only emitted as a posting when it carries a posting-shaped signal (gender marker in the anchor, or JSON-LD JobPosting on the fetched page) -- and a link that fails that test is still enqueued as a list page rather than dropped, so 36202 reaches /alle-stellenangebote and recovers its 9 missing vacancies
- [ ] #2 JOB_PATH's /detail/ alternative requires a job-ish parent segment, and NOT_JOB_PATH excludes glossary/news/press/event paths; klinikum-msp.de's glossar/detail/fusspflege no longer qualifies
- [ ] #3 sonstige_pflege's fallback behaviour is decided explicitly: either it stops being a catch-all for any Pflege token, or it joins excluded_role_classes -- state which and why, and pin it with the 'PFLEGEN KÖNNEN.' case
- [ ] #4 The ~120 existing junk rows are retro-purged; report the count removed per clinic and the new open_jobs total
<!-- AC:END -->
