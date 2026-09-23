---
id: TASK-123
title: >-
  Audit: crawl layer must not silently filter real postings; matcher/extractor
  owns all content-based accept/reject decisions
status: To Do
assignee: []
created_date: '2026-09-23 03:37'
labels: []
dependencies:
  - TASK-90
priority: high
ordinal: 123000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every content-based (not pure URL/path-structural) accept-or-reject regex/check inside the crawl/discovery layer (crawlers/vendor_adapters.py, crawlers/portals.py, pflege_jobs/sources/*.py -- GENDER, JOB_TEXT, NOT_JOB_TITLE_RX, WALL_MARKERS and any others found) is catalogued with file:line and classified as either (a) a structural link-shape signal needed to keep news/glossary/signup pages from being stored as fake postings, or (b) a content-guessing completeness heuristic that can silently drop a real posting
- [ ] #2 crawlers/vendor_adapters.py's GENDER and pflege_jobs/sources/career_crawl.py's JOB_TEXT (two independently-drifted copies of the same 'is this a real gendered job title' signal, confirmed different regexes as of TASK-90) are reconciled: either unified into one shared definition both import, or the reason they must differ is documented
- [ ] #3 For each heuristic kept in category (b), its real false-negative rate is measured against at least 2 live boards by comparing the crawler's own candidate-link count to what it actually stores as rows (same method TASK-90 used on barmherzige-bieten-zukunft.de: GENDER alone was silently dropping 105 of 147 real candidate links, only found by counting, not by unit-testing the regex in isolation) -- not just asserted safe from a unit test
- [ ] #4 The matcher/extractor layer (pflege_jobs/classify.py's classify_role/classify_employer, pflege_jobs/registry.py's Matcher) is fed the FULL unfiltered candidate set from at least 2 real boards (not pre-narrowed by crawl-layer heuristics) and verified to classify/match correctly, proving it does not silently rely on upstream crawl-layer filtering to keep out non-nursing or malformed content
- [ ] #5 A short doc note (docs/scraping.md or similar) states which layer owns which decision -- crawl layer: is this URL a candidate posting page at all; classify.py: is it Pflege and what role; registry.py Matcher: which clinic -- so a future change lands in the right place
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Trigger 2026-09-23, Ivan: surprised the crawler does detailed content regexing at all (GENDER-as-posting-gate) -- expected all content-based filtering to live in the matcher/extractor, not the crawler. Immediate motivating finding (TASK-90, same session): crawlers/vendor_adapters.py's GENDER regex (used across ~12 call sites to decide 'is this a real single posting page, not an index/listing page') missed the plain German bare-slash gendering convention (Pfleger/in, Pfleger/innen, Angestellte/r) entirely -- silently treated 105 of 147 real candidate links on barmherzige-bieten-zukunft.de as index-page noise and recursed into them instead of storing rows, undercounting one board by more than 2x. Partially patched in TASK-90 (added /in, /innen, /r to GENDER) but that was a narrow, reactive fix for one board's observed titles, not the systemic audit this task asks for. Related but distinct from TASK-11/TASK-95 (raw-first pipeline, stop filtering role/Pflege classification INSIDE app/crawl.py's inbox-write step) -- those cover 'do not drop an already-discovered row based on Pflege classification before storage'; this task is one layer earlier: 'does the crawler's own row-DISCOVERY logic (deciding which URLs even become candidate rows) silently miss real postings that were never discovered at all'. TASK-95 does not touch this layer.
<!-- SECTION:NOTES:END -->
