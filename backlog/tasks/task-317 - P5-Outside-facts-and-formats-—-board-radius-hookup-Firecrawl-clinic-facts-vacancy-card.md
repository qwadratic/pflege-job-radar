---
id: TASK-317
title: >-
  P5: Outside facts and formats — board radius hookup, Firecrawl clinic facts,
  vacancy card
status: To Do
assignee: []
created_date: '2026-09-26 08:48'
labels:
  - data
  - later
dependencies: []
priority: low
project: whatsapp
ordinal: 5
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
These plug outside facts and formats into the harness, and they depend on work outside it.

**Radius**
- This is a board task; Ivan builds it separately.
- Today the board's radius is client-side haversine over the 2,243 town coordinates in web/index.html (TOWNS / GEO_TOWNS). /api/clinics has no radius parameter.
- The harness uses its own radius: centre = mean lat/lon of the city's postings, LUNA_WARMING_RADIUS_KM default 30. The two can disagree.
- Once the board exposes a radius, plug it into:
  - the warming shortlist;
  - the search tool;
  - the yellow-flag policy (P2).
- Stopgap for an urgent case: reuse the town coordinates from web/index.html.

**Firecrawl clinic facts** (was TASK-311)
- Objective clinic information from the internet, for the close-time fact check.
- correction_turn.compare() on the P2 branch has a marked plug-in point.

**Vacancy card** (was TASK-310)
- A compact card, as an image or a text memo, as the answer to detail questions about a proposed vacancy.
- Design only for now.

Folded here: TASK-310, TASK-311. Their full text is kept in the archive.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 After the board ships its radius, the harness warming shortlist, search tool and yellow-flag policy use the board's radius
- [ ] #2 compare() receives scraped clinic facts with their source URL; a scrape failure is recorded on the card, never silently skipped
- [ ] #3 A design doc for the vacancy card covers content fields, image vs text, how it is produced and sent through the rail, and how the bench tests it
<!-- AC:END -->
