---
id: TASK-16
title: >-
  Feed inventory across all boards: RSS, Atom, sitemap, JSON feeds;
  robots-blocked boards route to Firecrawl
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
labels:
  - harvester
dependencies: []
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked (2026-09-09) to check everywhere whether an RSS feed or similar unusual source exists, because a board that forbids listing pages in robots.txt but allows detail pages can still be enumerated from a sitemap or a feed. München Klinik's robots.txt disallows /*json* and /api-* for everyone and names JobCrawlerBot and similar bots with Disallow: /; its HTML /stellenmarkt/ is allowed, so it is not blocked, but the pattern will recur. Rule set by Ivan: robots.txt is respected; a board whose robots.txt blocks the only usable path is the one exception where Firecrawl is used, and an IP ban is escalated as a complaint to the hoster. The inventory covers every distinct careers_url in the live clinics table (221) and records, per board: robots.txt verdict for our paths, sitemap.xml presence and whether it lists job URLs, RSS/Atom/JSON feed presence and what it carries, and the resulting enumeration source.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every board in the live registry has a recorded robots verdict, sitemap verdict and feed verdict with the URL probed
- [ ] #2 Boards whose only enumeration path is robots-blocked are listed as the Firecrawl exception set with the blocking line quoted
- [ ] #3 Boards with a usable feed or sitemap have that source recorded as the preferred enumeration variable for their family
<!-- AC:END -->
