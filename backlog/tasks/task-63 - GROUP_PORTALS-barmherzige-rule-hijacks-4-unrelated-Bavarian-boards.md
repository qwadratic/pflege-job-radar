---
id: TASK-63
title: GROUP_PORTALS barmherzige rule hijacks 4 unrelated Bavarian boards
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:06'
updated_date: '2026-09-18 10:46'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 63000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18. crawlers/vendor_adapters.py GROUP_PORTALS entry {"match": r"barmherzige"} (used by group_portal_for at :1450/:1456) matches on clinic name+careers_url, so any clinic whose name contains "barmherzige" is routed to karriere.barmherzige.net (the Barmherzige Schwestern order) even when it runs its own board and belongs to the unrelated Barmherzige Brüder order. Confirmed live: 4 clinics never get their own board fetched — 36201 Krankenhaus Barmherzige Brüder Regensburg (985 beds, 52 own postings/23 nursing), 26301 Klinikum St. Elisabeth Straubing (475 beds, 27/13), 37601 St. Barbara Schwandorf (267 beds, 33/8), 16214 Krankenhaus Barmherzige Brüder München (404 beds, 10/6) — 2131 beds total, ~122 own live postings missing every night. In their place, karriere.barmherzige.net own 41-82 Munich-area postings (Neuwittelsbach, Maria-Theresia-Klinik, plus Alten-/Pflegeheime) are ingested with each hijacking clinic own name as employer and matched R1_exact score 1.0 to the wrong clinic. See /tmp/crawler_review_2026-09-18.md section "crawlers/vendor_adapters.py" lines :1450/:1456 for full evidence.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 group_portal_for matches a GROUP_PORTALS pattern against the careers_url host (or requires the group host to already appear in careers_url), not against the clinic name, so a clinic with its own working board is never rerouted
- [x] #2 The 4 Barmherzige Brüder clinics (36201, 26301, 37601, 16214) are fetched from their own boards on the next scheduled adapter run, and their own postings (measured today: 52/27/33/10 live postings) appear attributed to the correct clinic
- [x] #3 karriere.barmherzige.net postings are attributed only to the Barmherzige Schwestern clinics that actually belong to that board, not to the 4 hijacked clinics
- [x] #4 app/crawl.py group-board fetch is deduped once per group (matching the existing group_done pattern in vendor_adapters.main()) so the same group URL is not refetched once per hijacking clinic
- [x] #5 A regression test asserts group_portal_for does not route a clinic whose own careers_url differs from the group host, using the Barmherzige Brüder/Schwestern pair as the fixture
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. crawlers/vendor_adapters.py group_portal_for(): keep the existing broad name+careers_url 'match' regex for CANDIDATE detection, but add an 'own_ok' guard -- when the clinic's own careers_url is non-empty, it must ALSO match a (possibly stricter) own_ok pattern (default: same as match) or the group route is refused, so a clinic's own working board always wins over a same-word group match.
2. Tightened the barmherzige entry's own_ok to the literal group host (karriere\.barmherzige\.net) -- the broad 'barmherzige' match alone would still have wrongly kept excluding St. Barbara Krankenhaus Schwandorf's OWN distinct microsite (barmherzige-bieten-zukunft.de), which merely shares the word.
3. Left kbo's own_ok defaulting to its existing broad match ('kbo-|kbo\.de') deliberately -- verified live that every registry kbo clinic's own domain (including 5+ kbo-branded satellite domains that just redirect into the shared listing) still contains that substring, so this changes nothing for kbo; one edge case (clinic 17106, own careers_url kinderzentrum.de, no kbo- substring) now correctly falls through to its own board instead of the group -- verified live (curl, 200, 289KB, real Stellenangebot/Pflege content) that this is a real, working, independent board, not a regression.
4. Added crawlers/vendor_adapters._group_list_url(c, g) shared by crawl_group_portal (extracted, no behaviour change) and the new per-run dedup cache, since the correct dedup key is the URL a clinic will ACTUALLY page (its own pre-filtered querystring when present, else the bare group list) -- deduping by the bare list alone would have wrongly skipped a second clinic's differently-filtered fetch.
5. app/crawl.py: added a group_cache dict created once per execute() run, threaded through _fetch_board -> _vendor_rows(..., group_cache=...), so several boards that independently route to the same group listing are fetched once and reused, not once per board; the fallback 'WARNING: 0 rows' log line is suppressed specifically for this dedup-skip case (it is not a board failure).
6. Also fixed the identical dedup-key bug in vendor_adapters.main()'s offline group_done cache (same file, same mechanism, one-line consistent fix).
7. Verified: (a) 5 new/updated tests in tests/test_completeness_group_portal.py -- group_portal_for refuses all 4 Barmherzige Bruder clinics, still routes the 2 real Barmherzige Schwestern members and a blank-careers_url/kbo-satellite clinic, plus a dedup-key test; (b) fixed 2 pre-existing tests in tests/test_crawl_board_retry.py whose mocked _vendor_rows signature broke on the new group_cache kwarg; (c) full offline suite -m 'not network': 992 passed / 1 skipped / 1 pre-existing unrelated failure (same as before this task, tracked as TASK-75 AC#1); (d) live read-only curl confirms all 4 previously-hijacked careers_urls return 200 with real content.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed group_portal_for() (crawlers/vendor_adapters.py) to require a clinic's own non-empty careers_url to also satisfy an 'own_ok' pattern (defaults to the group's broad match, tightened to the literal host for barmherzige) before taking the group route -- a clinic's own working board now always wins. All 4 Barmherzige Bruder clinics (16214 Muenchen, 36201 Regensburg, 26301 Straubing, 37601 Schwandorf, 2131 beds) no longer route to karriere.barmherzige.net; the 2 real Barmherzige Schwestern members (16219, 16226) still do. Live curl confirms all 4 clinics' own careers_url return 200 with real content. Also fixed the underlying dedup-key bug (a group fetch was keyed by the bare group list URL, which would have skipped a second clinic's differently-filtered fetch) via a new shared _group_list_url() helper, used both in app/crawl.py's new per-execute()-run group_cache (so kbo's 5+ satellite-domain boards fetch the shared listing once, not once per board) and in vendor_adapters.main()'s offline group_done cache. Verified: 5 new/updated tests in tests/test_completeness_group_portal.py (routing refusal for all 4 hijacked clinics, continued routing for the 2 real members + a blank-careers_url clinic + a kbo satellite domain, and a dedup-key distinctness test); fixed 2 pre-existing tests in tests/test_crawl_board_retry.py whose mocked _vendor_rows signature broke on the new group_cache kwarg; full offline suite -m 'not network': 992 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:FINAL_SUMMARY:END -->
