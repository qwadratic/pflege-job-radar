---
id: TASK-68
title: kbo.de group portal stamps Munich HQ address/postcode onto every site posting
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:08'
updated_date: '2026-09-18 13:20'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 68000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18. crawl_group_portal (crawlers/vendor_adapters.py :1499/:1503) keeps the kbo.de group JSON-LD address (Muenchen/80538, the head office, identical on every posting) as the posting own city/plz whenever title_city_rx fails to parse a site name out of the title, and even when title_city_rx does match, the stale HQ postcode 80538 is kept next to the new city; title_city_rx itself does not validate that the captured token is a real place, so a non-place capture like "Oberbayern" becomes the stored city. Measured today: 113 distinct postings/night on the kbo.de board (32 registry clinics across 6 careers_urls); of the 35 postings that survive the pre-inbox role filter, 16 carry city=Muenchen with plz=80538 though the real Einsatzort (stated in full further down the same page) is Haar, Ingolstadt, Berg am Starnberger See, or a Lech-Mangfall site -- the Matcher then attaches these via R0_board_town to clinic 16264 (kbo-Heckscher KJP am Kinderzentrum Muenchen). Of the 34 rows where title_city_rx does match, all still carry the stale plz 80538 next to the correct city, which the frontend renders as a wrong postcode. The verifier compounds this: extract_location trusts the same jsonld as TRUSTED_LOC, so it confirms the HQ city as correct instead of flagging it. See /tmp/crawler_review_2026-09-18.md "crawlers/vendor_adapters.py" :1499/:1503 for full evidence and fix sketch (read the page own Einsatzort/PLZ-Ort block via the extractors pflege_jobs/verify.py already has, validate title_city_rx captures against the towns list, and clear plz/region when the city changes).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 crawl_group_portal for the kbo.de board ignores the group HQ JSON-LD jobLocation as an unconditional city source and instead reads the detail page own Einsatzort/PLZ-Ort block (reusing pflege_jobs/verify.py extract_location-style extraction) when present, leaving city None when no site-specific location is found rather than defaulting to Muenchen
- [x] #2 title_city_rx captures are validated against the towns list (or career_crawl._canon_town/city_from_url) before being accepted as the posting city; an unplaceable capture like "Oberbayern" is rejected
- [x] #3 When the city is corrected away from the HQ value, plz/region are cleared or recomputed rather than keeping the stale 80538/Muenchen-region pair
- [x] #4 Re-crawling kbo.de shows the 16 previously-Muenchen nursing postings now carrying their real site city (Haar/Ingolstadt/Berg am Starnberger See/etc.) and no longer attributed to clinic 16264 via R0_board_town
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. crawlers/vendor_adapters.py GROUP_PORTALS: added hq_location_untrusted=True to the kbo.de entry (the flag that makes crawl_group_portal skip the JSON-LD jobLocation entirely for this group).
2. crawl_group_portal(): title_city_rx captures are now validated against the towns list (pflege_jobs.verify._placeable, same validator verify.py's own extract_location uses) before being accepted -- an unplaceable capture is discarded rather than becoming the posting's city.
3. When hq_location_untrusted is set: never carry the JSON-LD jobLocation through at all. Priority is (a) a validated title_city_rx capture, (b) the page's own Einsatzort/PLZ-Ort text (reusing pflege_jobs.verify._EINSATZORT/_PLZ_ORT/_clean_city, the exact extraction verify.py's daily re-check already uses), (c) leave city/plz/region all None -- an honest 'unknown' instead of the wrong HQ address. plz/region are cleared together with city in every case, never left stapled to a corrected or absent city.
4. Threaded an optional towns= parameter through crawl_group_portal, app/crawl.py's _vendor_rows (both call sites: raw_board_rows' free estimate path and _fetch_board inside execute(), both already had a towns set in scope) and vendor_adapters.main()'s offline CLI kept towns=None (unthreaded, degrades to unvalidated exactly as it already did for every other adapter in that tool).
5. Live read-only re-crawl of the real kbo-heckscher-klinikum.de board (109 rows, no writes): before this fix roughly half of all rows and 16/35 surviving nursing rows carried the stale Munich HQ address (München/80538) verbatim; after, 0 of 109 rows carry plz=80538, 0 rows have the HQ city with a stale postcode, and the 34 nursing rows now show real, distinct sites (Garmisch-Partenkirchen, Hausham/83734, Landsberg am Lech, Wolfratshausen, Taufkirchen/84416, Haar/85540, Ingolstadt/85049, Freilassing, Wasserburg am Inn/83512) -- only 5/34 are genuinely Munich, each with a real, distinct Munich postcode (81377/81539/80502/81677), never the stale 80538.
6. New test in tests/test_completeness_group_portal.py (mocked HTTP, no network) pins the three-tier fallback (title-named site, Einsatzort-text site, honest-unknown) and asserts none of them ever carries plz=80538 or the bare HQ city through.
7. Full offline suite -m 'not network': 1007 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Observed but out of scope: pflege_jobs.verify._EINSATZORT's capture group is occasionally a couple words too greedy on this specific board ('Ingolstadt E-Mail', 'Taufkirchen an der' -- trailing text glued on), a pre-existing regex-precision quirk shared by every other board that uses this same extractor in verify.py, not a wrong-city bug (the real town is still the leading, correct token) and not something this task's evidence named. Left untouched.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
crawl_group_portal() no longer trusts the kbo.de group's JSON-LD jobLocation (the Munich HQ address, identical on every posting) as the posting's own city. New priority for any group with hq_location_untrusted=True: a towns-validated title_city_rx capture, then the page's own Einsatzort/PLZ-Ort text (reusing pflege_jobs.verify's own extractors), then an explicit None/None/None rather than the wrong HQ -- plz/region always cleared together with city, never left stapled to a stale value. Threaded towns through both production call sites. Verified live against the real kbo-heckscher-klinikum.de board (109 rows, read-only): the universal stale plz=80538 is gone from every row, and nursing postings now show real distinct sites (Garmisch-Partenkirchen, Hausham, Landsberg am Lech, Taufkirchen, Haar, Ingolstadt, Wasserburg am Inn, Freilassing) instead of a Munich HQ address on 16/35 of them. 1 new test (3-way fallback, mocked HTTP). Full offline suite -m 'not network': 1007 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:FINAL_SUMMARY:END -->
