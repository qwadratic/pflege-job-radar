---
id: TASK-165
title: >-
  905 open postings from a 2026-09-24 StepStone search crawl: never verified, no
  clinic, one with a test-fixture URL
status: To Do
assignee: []
created_date: '2026-09-29 14:36'
labels:
  - db-quality
  - verify-freshness
dependencies: []
priority: high
ordinal: 163000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-29 while computing board stats. 905 of 3790 open postings (24%) have verify_status NULL, clinic_id NULL and first_seen 2026-09-24. Their observations (1017, source_id=20 employer_ats) come from StepStone city-search pages (payload.crawl.seed = https://www.stepstone.de/jobs/pflegefachkraft/in-<city>, collector playwright-egress-v1, all observed 2026-09-24T17:xx). Employers are mostly non-clinic (BRK, Korian, RENAFAN, Schön Klinik via StepStone). At least one posting carries external_url https://x.example/job/0 (test-fixture URL in production).

Two problems:
1. Source policy: the board is career-sites only; StepStone was retired as an aggregator. Unknown who ran this crawl and whether it was meant to write to production.
2. Freshness gap: the nightly verify schedule targets a clinic-id list, so postings with no clinic_id are never re-verified and stay "open" forever regardless of source.

Needs Ivan's decision before any write: keep (and verify) or retire these 905.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Origin of the 2026-09-24 StepStone batch identified (session/tool, intended or not)
- [ ] #2 Ivan decides keep vs retire; decision applied with a backup, live-verified
- [ ] #3 Postings with clinic_id NULL are covered by the nightly verify pass, or an explicit decision is recorded that they are not
- [ ] #4 The x.example test-fixture posting is removed and the path that let a fixture reach production is found
<!-- AC:END -->
