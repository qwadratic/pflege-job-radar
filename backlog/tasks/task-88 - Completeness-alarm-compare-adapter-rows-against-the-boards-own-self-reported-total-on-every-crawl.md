---
id: TASK-88
title: >-
  Completeness alarm: compare adapter rows against the board's own self-reported
  total on every crawl
status: To Do
assignee: []
created_date: '2026-09-21 04:26'
labels: []
dependencies: []
ordinal: 88000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, section 7. This is the cheapest possible answer to 'are we parsing this source in full', and it needs no oracle, no Firecrawl and no credits.

Most boards publish their own total: page.total (b-ite), numberOfItems (softgarden feed), TotalJobsCount (Duet/bezirkskliniken), X-WP-Total (WordPress REST), '44 Treffer' (BEESITE), 'Derzeit gibt es 57 offene Stellen' (several CMS boards), and rexx/oracle paginated counts. Where the audit compared adapter output against that number, the match was EXACT 17 times and a hard failure 9 times -- so the signal is both available and discriminating.

Proven full at audit time: Roth 9/9, Kliniken Südostbayern rexx 51/51, Schön Klinik 292/292, BG Murnau 377/377, Asklepios 1398/1398, mein-check-in Landshut 22/22, Amberg 47/47, Straubing 27/27, Neumarkt 44/44, Traunstein 51/51, BEESITE Ansbach 44/44, Bayreuth softgarden 114/114, Deggendorf 28/28, Landsberg 22/22, Passau 17/17, Coburg 78/78, Dachau 33/33.

Proven NOT full: 66101 (0 vs 62), 66301 (1 vs 51), 76201 (2 vs 16), 77406/76114/76203 (0 vs 57), 56201 (2 vs 31), 27501 (2 vs 27), 57705 (6 vs 53), 16233/37202/57408 (73/85/88 CMS pages vs an Oracle board of 1,122), 16201/16203 (walk aborts on the first posting page).

Unknown and honestly so: 47601 and 67601 -- helios-gesundheit.de returns Akamai 'Access Denied' to plain curl AND to a real Chromium render from this IP, exactly as crawlers/routing.py:90 WALLED documents. No Helios count can be trusted without non-datacenter egress or a Firecrawl rung.

This task is the mechanism that makes 'we parse every source in full' a continuously-verified claim rather than a one-off audit.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every adapter that can read its board's self-reported total does so and records it alongside the row count it returned
- [ ] #2 A crawl where rows < self-reported total is recorded as incomplete (a crawl_issue and a truncated-style flag), never as a plain success
- [ ] #3 Adapters whose boards publish no total are listed explicitly, with what alternative completeness evidence each one can offer -- an honest 'unknown' is acceptable, a silent assumption of completeness is not
- [ ] #4 The ratio is surfaced per board where coverage is judged, so a board that starts under-reading is visible the same day rather than at the next audit
- [ ] #5 Re-run across all boards and publish the full/not-full/unknown split as the standing answer to 'do we parse every source in full'
<!-- AC:END -->
