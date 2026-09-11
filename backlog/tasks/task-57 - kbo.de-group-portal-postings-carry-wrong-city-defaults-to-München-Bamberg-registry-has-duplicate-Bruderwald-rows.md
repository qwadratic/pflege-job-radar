---
id: TASK-57
title: >-
  kbo.de group-portal postings carry wrong city (defaults to München); Bamberg
  registry has duplicate Bruderwald rows
status: To Do
assignee: []
created_date: '2026-09-11 14:41'
updated_date: '2026-09-11 15:08'
labels: []
dependencies:
  - TASK-51
ordinal: 57000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Surfaced 2026-09-11 doing a board-aware re-verification of TASK-51's fuzzy-matched postings. Two separate real gaps found, neither fixed:

1. kbo.de group portal (crawl_group_portal in crawlers/vendor_adapters.py, GROUP_PORTALS['kbo.de'], 32-clinic shared board): postings' stored city is 'München' even when the job is clearly elsewhere -- e.g. posting external_url '.../26-19-pfk-dn-gap-pflegefachkraft-als-dauernachtwache-mit-schwerpunkt-pausenabloesung-m-w-d-in-garmisch-partenkirchen' (URL slug names Garmisch-Partenkirchen explicitly) has city='München' stored. With the wrong city, Matcher.match()'s board-town disambiguation can't narrow the 32-clinic pool (8+ candidates share town='München' alone) and correctly returns None rather than guess -- meaning at least 9 kbo.de postings that WERE previously matched (likely via some other path, possibly a now-fixed false-positive, not yet confirmed which) currently sit unmatched. Root cause is upstream of Matcher: whatever parses each job's JSON-LD off kbo.de/karriere/jobs/<slug> is either not reading the real jobLocation, or the group-portal crawl only ever knew the parent group's HQ city and never the individual site.

2. Klinikum Bamberg registry has two near-duplicate rows for the same physical site: 46101 'Klinikum Bamberg - Betriebsstätte am Bruderwald-' (trailing hyphen, likely a parse artifact) and 46170 'Klinikum Bamberg - Betriebsstätte am Bruderwald' (no trailing hyphen) -- both share careers_url https://www.sozialstiftung-bamberg.de/stellenangebote/. A posting whose employer text names 'Bruderwald' now correctly returns None (genuinely ambiguous between 2 candidates that are, as far as can be told, the same real site) rather than guessing. 5 postings affected.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 kbo.de group-portal job parsing reads each posting's real per-job city (not the group HQ or a stale default) -- check crawl_group_portal / parse_dvinci-adjacent parsing for this vendor in crawlers/vendor_adapters.py
- [x] #2 Bamberg Bruderwald duplicate rows (46101 vs 46170) resolved -- confirm with Bayern Krankenhausplan source whether they're truly the same site (merge/retire one) or genuinely distinct (find the real distinguishing fact)
- [ ] #3 Once fixed, re-run the 14 affected postings (9 kbo.de + 5 Bamberg, ids saved this session in /tmp/relink_final.json -- not committed anywhere durable, re-derive from postings where clinic_match_rule is null and employer mentions 'Bezirks Oberbayern' or 'Bamberg' if that file is gone) through Matcher.match() and correct clinic_id if a real answer is now findable
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11: implemented decision-4's mechanism in pflege_jobs/registry.py's R3/R4 token-overlap loop -- when multiple candidates tie and exactly one has real bed capacity (beds truthy), prefer it over beds-less near-duplicates regardless of operator match (generalizes beyond same-operator R6, since the Bamberg case actually has a THIRD candidate under a different operator -- a beds-less KJP day-clinic sharing the building name -- contaminating the same-operator tie-break). Verified locally with a 3-way-tie fixture reproducing the exact live shape; regression test added (test_mech_clinic_link.py::test_prefers_real_site_over_beds_less_duplicate). NOT yet delivered live -- Supabase (both the read proxy and the direct write host) has been timing out since ~15:00 UTC this session, unrelated to this change. Kbo.de AC #1 (title-city extraction) is also implemented and verified against ONE live sub-board (kbo-lmk.de: 15/36 matched) before the outage started; the other 3 kbo sub-boards (IAK/ISK/Heckscher) still need a delivery run once the DB is reachable again.
<!-- SECTION:NOTES:END -->
