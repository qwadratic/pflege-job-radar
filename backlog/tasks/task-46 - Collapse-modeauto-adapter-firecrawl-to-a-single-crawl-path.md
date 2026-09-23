---
id: TASK-46
title: Collapse mode=auto|adapter|firecrawl to a single crawl path
status: Done
assignee: []
created_date: '2026-09-11 05:01'
updated_date: '2026-09-22 18:59'
labels:
  - harvester
dependencies: []
ordinal: 46000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Judgment: mode switching, spend_gate 'adapter covers it' probing, /api/crawl/estimate, hunter (5 routes + 10 settings knobs), campaign and the 3-tier kill switch all exist because adapters were unreliable. Measured: Firecrawl's real scope is 11 of 407 clinics; the live schedule is mode=adapter, credits 0; hunter daemon is not installed. A green completeness verdict already proves a board is covered, no live probe needed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 POST /api/crawl has no mode parameter; routing decides adapter vs Firecrawl from the harvest_report verdict, Firecrawl only for the 11 unroutable/walled clinics
- [ ] #2 hunter, campaign, /api/crawl/estimate, fetch_details/deep are removed; weekly_budget + reserve_credits are the only settings kept
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closed as an abandoned architecture bet, contradicted by this session's own work: mode=auto|adapter|firecrawl were used deliberately and separately throughout 2026-09-22 (e.g. a mode=firecrawl-only scoped rerun on 5 walled clinics, a mode=adapter full run that intentionally never touches Firecrawl). The premise that a green completeness verdict alone can replace live per-mode routing was not borne out in practice.
<!-- SECTION:FINAL_SUMMARY:END -->
