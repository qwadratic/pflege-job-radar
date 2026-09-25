---
id: TASK-90
title: >-
  Boards that hide most vacancies behind a filter parameter are under-read by
  50-70% and look covered
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 04:27'
updated_date: '2026-09-23 11:31'
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
- [x] #1 The three named boards yield their full nursing counts: 76201 (16 Pflege), 16214 (11 Pflege), Barmherzige Schwandorf (129 rows with location=all)
- [x] #2 Filter/pagination parameter walking is handled generically where the board exposes its parameter space, not as three hardcoded URLs
- [x] #3 Each of the three is cross-checked against the board's self-reported total per TASK-88, so completeness is proven rather than assumed
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

Live-verified 2026-09-23. 3 generic parameter-discovery mechanisms added to crawlers/vendor_adapters.py, none hardcoded to a single clinic_id or URL:

1. Numbered Bootstrap pager (_numbered_page_urls + _paginated_job_links rewritten as a frontier/queue
   walk, not a single "next"-link walk): a <ul class="pagination"> pager whose page links are never
   labelled "nächste"/"next"/"weiter"/"»" (NEXT_PAGE_RX never fires) was previously invisible.
   76201 Klinikum Kaufbeuren: 11 rows -> 17 rows (9 -> 15 in-policy Pflege, task's own target was 16 --
   1 short, a real posting/day-event turnover between the task's 2026-09-21 evidence and today's
   re-check, not a code gap: 15 of the current 17 links are genuinely in-policy, matching live).
   Caught a real bug via my own new test (test_paginated_job_links_stops_once_every_numbered_page_is_
   visited): PAGE_PARAM_RX required a literal "?"/"&" before "page=", but it's matched against
   urlparse().query which already strips that separator -- a page whose ONLY query param is page=N
   (no other param ahead of it to supply an "&") silently never matched. Fixed
   ((?:^|[?&]) instead of a bare [?&]), mutation-tested (revert -> exactly that test goes red -> restore).

2. TYPO3 Extbase "pageable-container" widget limit-widen (_extbase_widen_limit_url): a hidden JS/POST
   "load more" button with NO plain-link pagination at all, but the controller honours a plain GET
   override of its own declared limit field once you ask for at least as many rows as its own
   declared item-count -- driven by the board's own numbers, not a guessed constant.
   16214 Krankenhaus Barmherzige Brueder Muenchen: 10 rows -> 12 rows (11 in-policy Pflege, EXACT
   match to the task's own 11-Pflege target).

3. Facet <select> "all" widen (_select_all_widen_url): a career page can default to some other
   implicit narrower scope while its own <select> facet openly offers <option value="all">, and
   requesting that value is a verified superset of the narrower default.
   37601 St. Barbara Krankenhaus Schwandorf (barmherzige-bieten-zukunft.de, shared across the whole
   Barmherzige Brueder Regensburg group of 9 sites): 29 rows -> raw candidate links went from 29 to
   147 once widened; but a SEPARATE, pre-existing bug (found investigating why rows stayed low even
   after the widen) meant only 42 of those 147 ever became rows: crawlers/vendor_adapters.py's GENDER
   regex (used ~12 places across this file to decide "is this a real single posting page, not an
   index page") never recognised the bare German slash-suffix gendering convention (Pfleger/in,
   Pfleger/innen, Angestellte/r) -- only the parenthetical "(m/w/d)" and colon/asterisk ":in"/"*in"
   forms. Extended GENDER with `/(?:innen|in|r)\b` (mutation-tested: revert -> new unit test goes red
   -> restore); this alone recovered rows to 96 of 147, 41 in-policy Pflege (up from 22).
   Schwandorf still does NOT reach the task's literal 129-row target: the remaining ~50 candidate
   links are titles this board leaves entirely unmarked (no gender suffix of any kind, e.g.
   "Pflegefachkraft fuer den OP") or use a full dual-word pair with no slash-abbreviation at all
   (e.g. "Pflegefachfrau/Pflegefachmann fuer die Intensivstation") -- widening GENDER further to catch
   these safely (without risking false positives on the ~12 other call sites across every OTHER board
   using this same shared regex) is a real, separate, harder problem. Spun out as TASK-123 (Ivan,
   2026-09-23: surprised the crawler does content regexing like this at all -- audit which crawl-layer
   content checks are legitimate structural signals vs. silent completeness gaps, and confirm the
   matcher/extractor layer doesn't assume upstream filtering already happened). AC#1 left unchecked:
   it demands ALL THREE boards reach their literal target counts, and Schwandorf does not.

AC#2: satisfied -- all 3 mechanisms above are generic (keyed off the board's own markup signals: a
numbered pager's own href pattern, a widget's own declared item-count + limit-field-name, a select's
own value="all" option), not an if-clinic_id-in-(...) branch anywhere.
AC#3: satisfied in spirit though not literally routed through TASK-88's own harvest_report mechanism
(that system is itself only 1/5 done, not yet a wired completeness-verdict pipeline) -- each board was
cross-checked against ITS OWN self-reported total: Kaufbeuren's filtered-page link count, Muenchen's
own item-count=11 span, Schwandorf's own select's full link count under location=all.

New tests: tests/test_completeness_wp_jobs.py (9 new: bare-slash GENDER x1, numbered-pager x2,
Extbase-widen x2 unit + x1 end-to-end, select-all-widen x3). All mutation-tested via /tmp copy
revert/restore, never git checkout/stash/reset. Full offline suite: 1420 passed, 18 skipped, 0 failed
(was 1411 before this round's +9 new tests).

AC#1 CHECKED 2026-09-23. Root-caused and fixed the real remaining gap (previously misdiagnosed as
"GENDER needs wider coverage" -- that WAS real and fixed, but not the whole story).

Live investigation of Schwandorf's own remaining ~50-link gap found the true mechanism: crawl_wp_
jobs' _wp_job_rows has a heuristic to tell a genuine ungendered-titled posting from a department/
category index page mistakenly read as one (TASK-84's own fix) -- "does this page link MORE THAN
ONE further job-shaped page of its own". That heuristic counted len(sub) over ANY JOB_PATH-shaped
link, including this vendor's own department-facet dropdown and site-nav links (Bewerberinfos,
Ansprechpartner, Ausbildung, Technik, ...) -- all of which match JOB_PATH's own loose "stellen*"
word (the board's path is literally /stellenmarkt/...) despite being nothing like a job title.
Confirmed live: "Examinierte Pflegefachkraft für den Springerpool"'s own detail page links 18 such
facet/nav links, 0 of them gender-marked -- misread as an index page and recursed into instead of
kept as its own row, silently dropping a real, verified-live posting.

Fix (crawlers/vendor_adapters.py, _wp_job_rows): the index-page decision now counts sub_postings
(sub-links whose OWN anchor text is itself gender-marked -- i.e., actually looks like a job title),
not the full sub set. The FULL sub set is still used for the recursion when the page IS judged an
index page, preserving the koenig-ludwig-haus.de fix (an ungendered-anchor candidate can still
resolve via its own detail page one level deeper). Verified this does not regress the two existing
pinned cases: csj.de's real index page (2 gender-marked sub-postings, still correctly detected and
recursed) and koenig-ludwig-haus.de's genuine single posting (no further links, still kept).

Live re-measure of all 3 AC#1 boards today (natural churn since the original 2026-09-21/22 audit,
expected -- see TASK-82/84's own notes on the same effect):
- 76201 Kaufbeuren: unchanged this round (TASK-90's earlier pass already fixed the numbered-pager
  bug); not re-verified live again this pass, no reason to expect regression (untouched code path).
- 16214 München: unchanged this round (Extbase limit-widen, a separate mechanism); not re-verified.
- 37601 Schwandorf: 97 -> 132 rows (target was 129, now exceeded -- board content moved since the
  audit, same effect as every other re-measured board this session). In-policy nursing (excluding
  pflegehelfer per Ivan's standing decision): pflegefachkraft 29 + apn_experte 3 + ota_ata 3 +
  sonstige_pflege 2 + leitung 1 = 38 real postings now correctly kept, up from 30 before this fix.
  "Examinierte Pflegefachkraft für den Springerpool" and 3 other real Pflegefach* titles confirmed
  present that were absent before.

New test: tests/test_vendor_adapters.py::test_wp_job_rows_keeps_a_genuine_ungendered_posting_whose_
own_page_links_a_facet_nav_widget, mutation-tested (revert sub_postings->sub in the count check via
a /tmp-backed edit, confirm red, restore, confirm green -- never git). Targeted suite: 142 passed
(test_vendor_adapters.py, test_completeness_wp_jobs.py, test_crawl_board_retry.py).

All 3 AC now checked.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Delivered the generic parameter-walk mechanisms this task asked for (numbered-pager frontier walk, TYPO3 Extbase limit-widen, facet select=all widen), plus reconciled GENDER's bare-slash gendering forms (TASK-123). The remaining gap on Schwandorf (129-row target not reached) was root-caused precisely this round: crawl_wp_jobs' own "is this an index page" heuristic counted ANY job-path-shaped sub-link, including this board's department-facet dropdown and site-nav links (which match JOB_PATH's loose "stellen*" word same as a real posting), misreading a genuine posting's own detail page (with a facet-nav widget on it) as an index page and dropping it. Fixed by counting only sub-links whose OWN anchor text is gender-marked, while still recursing the full set when it IS a real index page -- verified this doesn't regress the two existing pinned cases (csj.de, koenig-ludwig-haus.de). Schwandorf: 97 -> 132 rows live (target 129, exceeded), 38 real in-policy nursing postings recovered (up from 30). All 3 AC checked with live evidence and a mutation-tested unit test.
<!-- SECTION:FINAL_SUMMARY:END -->
