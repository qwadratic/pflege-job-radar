---
id: TASK-311
title: Objective clinic facts for the close check via Firecrawl
status: To Do
assignee: []
created_date: '2026-09-25 17:56'
updated_date: '2026-09-26 08:49'
labels: []
dependencies: []
project: whatsapp
ordinal: 264000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The correction turn compares our claims against posting text and the board clinic registry only. Ivan wants objective clinic information from the internet as well; Firecrawl is the candidate source. correction_turn.compare() has a marked plug-in point.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 compare() receives scraped clinic facts with their source URL
- [ ] #2 Scrape failure is recorded loudly on the card, not silently skipped
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26: folded into TASK-317 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
