---
id: TASK-61
title: >-
  Verification: sites that still need a bespoke rung, and the drift guards after
  the 2026-09-16 cleanup
status: To Do
assignee: []
created_date: '2026-09-16 22:40'
labels: []
dependencies: []
ordinal: 61000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up from the 2026-09-16 posting-DB cleanup (Ivan: "status must be usable as the only filter").
The table is currently in the target state -- 2925 postings, all Bavarian, all with a verdict from a
real request, 0 open-but-not-live -- so this task is about keeping it there and about the handful of
sites whose verification depends on a vendor-specific rung rather than the generic ladder.

VERIFIED ONLY VIA A BESPOKE RUNG (works today, but breaks silently if the vendor changes):
- logaallin.regiomed-kliniken.de (P&I LOGA "bewerber-web", GWT, 38 postings, clinics 46301/47801):
  no per-posting page exists at all; liveness = "is the title still in the rendered list", rendered
  with networkidle + scrolling the way pflege_jobs/sources/pi_asp.py does it. A plain render sees an
  empty 7KB GWT shell. If the list markup changes, all 38 fall back to 'error' at once.
- www.helios-gesundheit.de (556 postings before the cleanup, the biggest single host): every page is
  an Akamai "Access Denied" to plain HTTP and to headless Chromium, including a stealth context.
  Only Firecrawl gets through, at ~1 credit per posting. After the cleanup 430 of those rows were
  deleted as non-Bavarian, so the recurring cost is small -- but any Helios posting that stays in the
  table can only ever be verified on the Firecrawl rung.

WATCH ITEMS:
- referral-portal-staging.lmu-klinikum.de had 5 postings in the production table. A STAGING host
  should not be a source at all; its 5 rows turned out to be dead and are now expired, but the
  clinic's careers_url should be checked so staging URLs stop entering.
- The daily mode=verify schedule (05:17 UTC, schedule id 2) records city mismatches and unverifiable
  pages in crawl_issues. That report is the drift signal; if a host starts appearing there in bulk,
  its rung broke.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 regiomed list-membership verification still resolves all of that board's postings (spot-check after any pi_asp change)
- [ ] #2 No staging/preview host appears as a source_url in postings
- [ ] #3 crawl_issues reviewed daily; a host appearing in bulk is triaged to the rung that broke
<!-- AC:END -->
