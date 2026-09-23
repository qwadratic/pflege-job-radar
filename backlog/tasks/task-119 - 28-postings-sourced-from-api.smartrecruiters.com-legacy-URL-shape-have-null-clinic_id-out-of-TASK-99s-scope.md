---
id: TASK-119
title: >-
  28 postings sourced from api.smartrecruiters.com (legacy URL shape) have null
  clinic_id, out of TASK-99's scope
status: To Do
assignee: []
created_date: '2026-09-22 20:25'
labels:
  - matching
  - data-quality
dependencies:
  - TASK-99
ordinal: 119000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-22 while verifying TASK-99's live backfill. jobs.smartrecruiters.com/ArtemedSE (the current, public listing-page URL shape) is fixed by TASK-99's VENDOR_ACCOUNT_POOLS -- 45+ postings now correctly resolve. But 28 of the 120 total smartrecruiters-sourced postings in pflege_jobs.v_postings carry a DIFFERENT source_url shape entirely: https://api.smartrecruiters.com/v1/companies/ArtemedSE/postings/<id> -- the raw vendor API endpoint, not the public jobs.smartrecruiters.com listing page. These predate this session's work and were presumably ingested by an older crawl path (a direct API probe, or an early Firecrawl agent run) that no longer runs today -- not reproduced by TASK-99's fix, not reproduced by any current crawl path found so far. Not investigated further: which collector/run originally wrote these, whether that path is still live anywhere, and whether the same VENDOR_ACCOUNT_POOLS pool (16228,16235,18105,18802,18808,18813,18872,76108) would resolve them the same way if re-matched (their employer_name/city fields were not inspected).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Which collector/ingestion path originally wrote these 28 api.smartrecruiters.com rows is identified (grep provenance/collector fields, check crawl_output/*.jsonl history if still retained)
- [ ] #2 Confirmed whether that path is still live today or fully retired -- if live, it needs the same account-pool fix TASK-99 applied at its own write point
- [ ] #3 The 28 rows' own employer_name/city are inspected; if content-matchable or board-poolable the same way as TASK-99, apply the fix and re-verify live in v_postings
<!-- AC:END -->
