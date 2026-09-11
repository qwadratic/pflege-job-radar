---
id: TASK-50
title: >-
  SPA/bot-walled career boards -- Asklepios (1035 beds), dbkg.de, Rotkreuzklinik
  Würzburg
status: To Do
assignee: []
created_date: '2026-09-11 10:49'
labels: []
dependencies: []
ordinal: 50000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-11 recon: three zero-yield boards look structurally different from the rest -- Asklepios (asklepios.com) renders as a client-side app shell (<div id="app"/root/...>) with no static job content; dbkg.de is the same shape; rotkreuzklinik-wuerzburg.de returns HTTP 403 outright to a plain requests fetch (same bot-wall pattern already documented for helios-gesundheit.de in crawlers/routing.py's WALLED regex). Asklepios alone is 1035 beds -- likely the single highest-value unfixed board in the registry. These need either a Playwright-based fetch, a different User-Agent/header strategy for the 403 case, or Firecrawl as the fallback (see TASK-41, Firecrawl as completeness oracle).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Asklepios board's real job API/widget endpoint identified and either wired into a new adapter or routed to Firecrawl
- [ ] #2 rotkreuzklinik-wuerzburg.de's 403 root-caused: confirm whether a different UA/header unblocks it, or add it to routing.py's WALLED set like helios
- [ ] #3 dbkg.de triaged the same way as Asklepios
<!-- AC:END -->
