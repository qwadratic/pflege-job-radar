---
id: TASK-54
title: >-
  Boards with zero 'pflege' mentions at all -- likely wrong careers_url or dead
  board
status: Done
assignee: []
created_date: '2026-09-11 10:50'
updated_date: '2026-09-11 13:42'
labels: []
dependencies: []
ordinal: 54000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-11 recon: 4 zero-yield boards returned HTTP 200 but the fetched page contains no occurrence of the word 'pflege' anywhere, unlike every other clinic career page in the registry. This is a stronger signal than a JS-widget gap -- it suggests the careers_url in the registry no longer points at a real careers/jobs page at all (redirected to a generic landing page, retired board, or wrong domain entirely). ukr.concludis.de is the highest-value one at 839 beds (Uniklinik Regensburg).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 ukr.concludis.de (839 beds): confirm current real career URL for Uniklinik Regensburg and fix the registry, or confirm concludis widget genuinely has no static fallback and needs the concludis-specific approach already used elsewhere
- [x] #2 www.artemedmuenchen.de, www.vital-klinik.de, www.310klinik.com: each checked for a live, correct careers page; registry updated or the board flagged dead
- [ ] #3 No fix applied blind -- each of the 4 confirmed by opening the actual current site before changing anything, since 'no pflege keyword' could also mean the clinic genuinely has zero pflege-department content by design
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
ukr.concludis.de fixed 2026-09-11: registry was pointed at ukr.de's OLD/decoy concludis page. Real career page is www.ukr.de/ausbildung-karriere/stellenangebote, which itself embeds a bite loader-v1 widget (customer=universitaetsklinikum-regensburg-anstalt-oeffentlichen-rechts, listing=main-listing) with a REAL 40-hex API key (unlike Feldafing/TASK-55's empty-key case) -- bite.py's existing api_key() extraction already handles this shape unmodified. Relabelled ats_type concludis->bite, careers_url fixed. Delivered live: raw=58, kept=18 nursing/ausbildung postings, 18/18 matched, 2 new.

artemedmuenchen.de (52 beds): confirmed non-bug -- same Artemed-group smartrecruiters decoy pattern as klinik-vincentinum.de/klinik-feldafing.de (company_code ArtemedSE), real jobs already covered via the shared board. Its own ats_type=bite label is simply wrong -- no bite widget present at all on this page.
www.vital-klinik.de (42 beds): confirmed genuinely empty -- page has no job listing content whatsoever, just nav/contact.
www.310klinik.com (41 beds): careers_url was a 2021 marketing blog post about a promotional tricycle vehicle, not a career page at all. Fixed to the real /karriere/ page -- which itself currently lists 0 open positions (just a 'send your CV' mailto), so no immediate delivery, but the registry now points at the right place for when they do post.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All 4 boards triaged. 1 fixed with real delivery (ukr.concludis.de, 18 postings). 1 URL corrected but currently 0 postings (310klinik.com). 2 confirmed non-bugs (artemedmuenchen.de decoy, vital-klinik.de genuinely empty).
<!-- SECTION:FINAL_SUMMARY:END -->
