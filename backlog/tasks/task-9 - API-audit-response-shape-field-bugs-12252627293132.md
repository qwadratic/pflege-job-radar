---
id: TASK-9
title: 'API audit: response-shape/field bugs (#12,25,26,27,29,31,32)'
status: Done
assignee: []
created_date: '2026-09-08 23:18'
updated_date: '2026-09-22 18:59'
labels:
  - api
dependencies: []
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Full findings: harness/evals/api-layer-audit-2026-09-08/findings.md. (12) GET /api/clinics?has_jobs=0 is a no-op (only has_jobs=1 handled) -- silently returns all clinics, breaks the exact lead-list recipe skill/SKILL.md documents. (25) size buckets hardcoded client-side (sizeOf()) duplicate and can drift from taxonomy.json's size_buckets already on every clinic row as .size. (26) run row/new counters renamed rows/new in coverage+billing vs n_rows/n_new everywhere else, actively misleading since 'rows' means the pagination array elsewhere. (27) POST /api/crawl vs GET /api/crawl/plan disagree on response key types for adapter/firecrawl (int vs list). (29) GET /api/schedules/presets exists, unused -- frontend hardcodes a stale copy. (31) next_run_at uses minute precision vs second precision everywhere else, breaks lexical timestamp comparisons. (32) autopilot stores Europe/Berlin timestamps compared lexically against UTC-bound filters -- silently wrong result sets.
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Merged into TASK-6 (renamed 'API audit follow-ups, consolidated') to cut task count -- content preserved verbatim as one of its 5 acceptance criteria, not lost.
<!-- SECTION:FINAL_SUMMARY:END -->
