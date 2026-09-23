---
id: TASK-90
title: >-
  Boards that hide most vacancies behind a filter parameter are under-read by
  50-70% and look covered
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:27'
updated_date: '2026-09-22 01:44'
labels: []
dependencies: []
ordinal: 90000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, finding M10. Not JS, not bot-walled -- plain HTTP, but the default view is a lie.

- Klinikum Kaufbeuren (76201): the default board page returns 11 links with ZERO nursing. The 16 Pflege rows only exist under ?selection3=3&page=N.
- Krankenhaus Barmherzige Brüder München (16214): serves 10 of 21 with no page links and an un-clickable pager; the 11 Pflege rows only appear under ?tx_oycimport_list[category]=15.
- Barmherzige Schwandorf: the board silently defaults to a single-location view, 30 rows against 129 with ?...[location]=all.

A crawler that fetches careers_url and walks anchors under-reports these by 50-70% while reporting success. This is the same class of failure as TASK-85 (silent near-zero yield) but with a different trigger: the board answers 200 with real job links, just not most of them.

Generalisation worth making rather than three one-off URL fixes: a board whose listing carries filter/pagination parameters should be walked across its parameter space, and the board's own total (TASK-88) is what proves the walk was complete.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The three named boards yield their full nursing counts: 76201 (16 Pflege), 16214 (11 Pflege), Barmherzige Schwandorf (129 rows with location=all)
- [ ] #2 Filter/pagination parameter walking is handled generically where the board exposes its parameter space, not as three hardcoded URLs
- [ ] #3 Each of the three is cross-checked against the board's self-reported total per TASK-88, so completeness is proven rather than assumed
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Establish live evidence for each of the 3 boards via free HTTP GET (and Playwright where plain HTTP shows no content): exact URLs, exact link/row counts default vs filtered, exact parameter names/encodings, pagination mechanism.
2. Cross-check evidence against the registry (data/registry/clinics.csv) to see which fixes are already applied there vs still missing.
3. Write the precise findings as an implementation spec into this task's notes for the adapter-owning agent (crawlers/vendor_adapters.py / career_crawl.py are out of this round's file ownership).
4. Determine whether any part of the fix lands in pflege_jobs/classify.py, patterns.json or app/data.py (this round's owned files) -- implement only that part, if any.
5. Leave acceptance criteria unchecked with an honest reason if the actual adapter-side implementation is out of scope this round; say clearly what was handed off.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Evidence gathered live 2026-09-22 (free HTTP GET + Playwright where plain HTTP shows nothing; 0 Firecrawl credits spent). This task's files (pflege_jobs/classify.py, patterns.json, app/data.py) own none of the fix -- confirmed nothing here needs a parameter walk or a listing parse. Full handoff below, precise per board.

=== 76201 Klinikum Kaufbeuren ===
Board: https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote (plain HTTP, TYPO3, no JS needed).
- No filter (?selection3 absent), page 1 of 5: 10 unique job links, 1 in-policy (Beleghebamme; rest are Ausbildung/BFD/Facharzt/Chefarzt -- correctly excluded).
- registry's CURRENT careers_url already carries ?selection3=3 (data/registry/clinics.csv:322 -- this part of the fix is already landed, was NOT missing). Page 1 under that filter: 10 DIFFERENT links, 9 in-policy Pflegefachkraft/OTA-ATA/Praxisanleitung (1 'Erlebnisbewerbertag' event day correctly excluded).
- ?selection3=3&page=2 (plain GET, found as a literal <a href> pager link on page 1: .../stellenangebote?selection3=3&page=2): 6 more links, all in-policy (5 Pflegefachkraft + 1 Praxisanleiter).
- TOTAL under the filter across both pages: 16 unique links, 15 in-policy -- reproduces the task's '16 Pflege rows' exactly.
- LIVE-RAN THE CURRENT ADAPTER (tools path, app.crawl.raw_board_rows against the registry's own careers_url, free): returns only 11 rows / 9 in-policy TODAY -- confirms page 2 is genuinely not being walked by the current code, not merely a stale claim. career_crawl.py's Crawler already has a generic PAGINATE regex ([?&](page|p|seite|...)=\d+) intended to follow exactly this kind of pager link (career_crawl.py:40,350) -- worth checking why it is not reaching page 2 on THIS board specifically (BFS ordering / depth_cap / list_budget interaction is my best guess, not verified: I did not dig further into career_crawl.py internals since it is outside this round's file ownership).
SPEC: no new filter parameter needed here (already in the registry) -- the gap is purely that GET .../stellenangebote?selection3=3&page=N (N=1,2; page 2 was the last one that returned rows, confirm the board's own boundary rather than assuming 2 is always the max) must be walked and its rows merged. This is the generic case: a plain GET pager already present as <a href> markup that the existing pagination-follow should handle once whatever is currently short-circuiting it is found.

=== 16214 Krankenhaus Barmherzige Brüder München ===
Board: https://karriere-barmherzige-muenchen.de/stellenangebote (plain HTTP, TYPO3 + oyc_template extension, no JS needed -- content IS in the static HTML, just paged).
- No category filter: <span class="item-count">21</span> total, but only 10 <li class="list-item"> entries render server-side (fixed page size).
- registry's CURRENT careers_url already carries ?tx_oycimport_list%5Bcategory%5D=15 (data/registry/clinics.csv:63 -- already landed). Under that filter: item-count drops to 11 (exact match to the task's '11 Pflege rows'), but STILL only 10 entries render -- 1 row is missing.
- Root cause of the missing row + the 'un-clickable pager': the form is a TYPO3 Extbase form (id="vacancy-filter", method="post", data-page-field-name="tx_oycimport_list[page]", data-limit-field-name="tx_oycimport_list[limit]") with a 'mehr Stellenangebote anzeigen' button that carries class="button pager-execute hide" -- it is HIDDEN by default and JS-triggered (no plain <a href> to a page 2 exists anywhere in the markup, unlike Kaufbeuren). Getting the 11th row therefore needs either (a) a POST to the form's action URL (https://karriere-barmherzige-muenchen.de/stellenangebote#vacancy-filter) with tx_oycimport_list[page]=2, tx_oycimport_list[category]=15, tx_oycimport_list[limit]=<current+1 or higher>, PLUS the page's own __trustedProperties/__referrer hidden-field values (standard TYPO3 Extbase CSRF-style token, scraped fresh from each page load -- I did not attempt to fully replicate this live, it needs real form-parsing code, not a hand-built curl call), or (b) simply requesting a higher tx_oycimport_list[limit] on the first GET (untested live -- worth trying first, cheaper than replicating the POST if the extension honours a limit override on GET).
SPEC: this one genuinely needs adapter code that parses the Extbase form's hidden fields and either raises the limit or resubmits page=2 -- not a trivial parameter substitution like Kaufbeuren.

=== 37601 St. Barbara Krankenhaus Schwandorf (registered as Barmherzige Schwandorf) ===
Board: https://www.barmherzige-bieten-zukunft.de/stellenmarkt/stellenboerse -- registry's careers_url (data/registry/clinics.csv:207) carries NO parameter yet, this part is NOT fixed in the registry.
CORRECTION to the task's framing: this board is NOT plain-HTTP-readable at all -- plain curl of the exact registry URL returns a full page shell (188KB, real header/nav/footer/Klaro cookie-consent scripts) with ZERO job content or even internal links anywhere in the static HTML (verified: 0 hrefs, 0 'pflege'/'w/m/d' mentions). It needed Playwright (crawlers.portals.fetch_page, already used elsewhere in this repo, free) to see any content at all -- this is the SAME class of gap as the already-solved JS-widget boards (TASK-49/50/77), layered UNDER the parameter-hiding this task describes, not instead of it.
Rendered via Playwright (wait_ms=3000 + networkidle):
- No location param: 30 unique job detail links -- reproduces the task's '30 rows' exactly. This portal is shared across the whole Barmherzige Brüder Regensburg group (confirmed via the rendered <select name="tx_jrpersisjobs_fejrpersisjobs[location]">: options are 'all', BBSG GmbH(6), CHA_MVZ(8), Klinik St. Hedwig Regensburg(2), Klinikum St. Elisabeth Straubing GmbH(4), Krankenhaus Barmherzige Brüder München(7), Krankenhaus Barmherzige Brüder Regensburg(1), Krankenhaus St. Barbara Schwandorf(5), MVZ Klinikum Straubing GmbH(3)) -- so the unfiltered default is NOT 'Schwandorf only' as the task assumed, it already spans several sites at some other implicit default; the exact default scope was not further isolated (out of scope for what this file's ownership needs).
- ?tx_jrpersisjobs_fejrpersisjobs%5Blocation%5D=all (rendered via Playwright, same wait): 129 unique job detail links -- reproduces the task's '129 rows' exactly.
- There is also a tx_jrpersisjobs_fejrpersisjobs[jobdescription] facet (values include '1'='Pflege- und Funktionsdienst', '4'='Ausbildung', '6'='Berufsfachschule für Pflege') that could narrow directly to nursing-relevant categories instead of pulling all 129 and classifying client-side -- not verified live (time-boxed), flagging as a possible optimisation rather than a requirement.
SPEC: this board needs (a) Playwright rendering (not plain HTTP) as a precondition, THEN (b) ?tx_jrpersisjobs_fejrpersisjobs[location]=all appended. Since this portal already serves Regensburg (36201) and Straubing (26301) too under ats_type=typo3_jobs/blank, this may be the SAME underlying board attribution question TASK-81 already tracks (one shared board, several clinic_ids) -- worth checking against TASK-81's findings before implementing, to avoid duplicating that work.

HANDOFF SUMMARY: all three boards' generic-parameter-walk implementation belongs in crawlers/vendor_adapters.py or pflege_jobs/sources/career_crawl.py (TYPO3/Extbase-family board reading), neither owned by this round's classify.py/patterns.json/app/data.py scope. Nothing here is implemented in code; this note is the full spec (exact URLs, exact live-verified counts, exact parameter names/encodings, and for München/Schwandorf the extra mechanism each one actually needs beyond a bare query string) for whichever agent owns those files this wave.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
All 3 acceptance criteria left UNCHECKED, honestly: they require the adapter itself to yield the full counts and to walk parameter space generically (AC#1, AC#2) and to cross-check against TASK-88's board-total signal (AC#3) -- all of that is crawlers/vendor_adapters.py / pflege_jobs/sources/career_crawl.py territory, explicitly outside this round's owned files (pflege_jobs/classify.py, patterns.json, app/data.py). Nothing in those 3 files was relevant to change for this task -- verified by reading it end to end, there is no classification or snapshot-serving angle here, only board-parameter-walking.

What I did deliver, as the task itself asked for: precise, live-verified evidence per board written into this task's notes --
- 76201 Kaufbeuren: registry fix (?selection3=3) already landed; the live-run current adapter still returns only 11/9 rows today (verified by running app.crawl.raw_board_rows against the real registry careers_url, free); page 2 of that same filter (a plain GET <a href> pager link, ?selection3=3&page=2) adds the missing 6 rows for 16/15 total, matching the task's numbers exactly.
- 16214 München: registry fix (category=15) already landed and correctly narrows 21->11 (exact match); but only 10 of 11 render server-side because the 'load more' control is a hidden, JS/POST-triggered TYPO3 Extbase form (tx_oycimport_list[page]/[limit]), not a plain link -- a harder case than Kaufbeuren's, spec'd with the exact form field names.
- 37601 Schwandorf: registry has NO parameter yet (not previously fixed); corrected the task's own framing -- this board is not plain-HTTP-readable at all (0 links/content in static HTML), it needed Playwright to show anything. Rendered via Playwright: default view = 30 rows (exact match), ?tx_jrpersisjobs_fejrpersisjobs[location]=all = 129 rows (exact match) -- both numbers reproduced live. Flagged a likely overlap with TASK-81 (shared-board attribution) since this portal also serves Regensburg/Straubing.

Handed off in full to whichever agent owns crawlers/vendor_adapters.py / career_crawl.py this wave. No production data read or written; only free GET/Playwright requests to public hospital career pages.
<!-- SECTION:FINAL_SUMMARY:END -->
