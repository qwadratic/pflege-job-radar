---
id: TASK-55
title: >-
  bite adapter: newer 'loader-v1' embed generation has no embedded API key (v5,
  niiid)
status: Done
assignee:
  - '@claude'
created_date: '2026-09-11 12:38'
updated_date: '2026-09-21 04:39'
labels: []
dependencies: []
ordinal: 55000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Discovered while triaging TASK-48 (Benedictus Krankenhaus Feldafing, clinic_id 18813, 70 beds, careers_url https://www.klinik-feldafing.de/karriere/stellenangebote). pflege_jobs/sources/bite.py's detect()/api_key() correctly recognize the newer data-bite-jobs-api-listing="{customer}:{listing}" embed (here: customer=artemed-8, listing=niiid) and correctly fetch https://cs-assets.b-ite.com/artemed-8/jobs-api/niiid.min.js -- but unlike the older bite embed generation this docstring documents (bundle embeds key:"<40 hex>"), this bundle calls t.createClient({key:""}) with an EMPTY key. It resolves a v5 API base (https://static.b-ite.com/jobs-api/v5/api-v5.js) via window.__$BiteJobsApiLoaderV1$__ at runtime instead -- the real per-tenant credential/endpoint is not present anywhere in the static HTML or the two JS bundles fetched so far; finding it needs either reading further into the v5 api-v5.js bundle for its own data-fetch endpoint convention, or a Playwright probe of what network calls the loader actually makes once it runs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Real v5 API endpoint (or confirmation there is none without a browser) documented for the niiid/loader-v1 bite generation
- [x] #2 Benedictus Krankenhaus Feldafing's real postings (if any) reachable via this adapter, or the board correctly flagged as needing Playwright
- [x] #3 Check whether other Artemed-group clinics share this same newer bite embed (the group also runs a separate smartrecruiters board for the same clinics, company_code ArtemedSE -- confirm the two aren't just duplicate postings of the same jobs before adding a second read path)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Fetch klinik-feldafing.de/karriere/stellenangebote and cs-assets.b-ite.com/artemed-8/jobs-api/niiid.min.js and read what the loader-v1 bundle actually does (cheaper than a Playwright probe if the static answer is conclusive).
2. Find where this board's postings really live; confirm on the page itself rather than by inference.
3. Fix the ROOT cause in the adapter that should already have covered it, not in bite.py if bite is not the vendor.
4. Freeze a trimmed real slice of the career page under tests/fixtures/board_samples/ and add offline tests; mutation-test them.
5. AC#3: check the two other Artemed-group clinics (16228, 16235) for the same embed, and whether the b-ite mount and the smartrecruiters board are duplicates of each other.
6. Reconcile data/registry/clinics.csv's ats_type with what the live pages prove.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11 further investigation: fully reverse-engineered the v5 API's real endpoint and request
shape (useful for any OTHER bite tenant on this same generation with a real key, not just Feldafing):
  POST https://jobs.b-ite.com/api/v1/postings/search
  body: {"key": "<the tenant's real key>", "filter": {...}}  (JSON)
  (also GET https://jobs.b-ite.de/api/v1/address-autocomplete for the location-filter UI, irrelevant
  to scraping)
Confirmed live: calling this with key="" (exactly what the niiid.min.js bundle hardcodes for
artemed-8/Feldafing) returns 400 {"error":"API key is missing"} -- the empty key is not a bug in our
reading of the bundle, the real API genuinely rejects it.

This means either: (a) the "niiid" listing type is not a standard job-search widget at all -- static.b-ite.com/niiid/v1
references a chatbot/"recruiting-assistant" product (dot.niiid.io, BiteChatbotV1) distinct from the
postingSearch API, so this specific Feldafing listing may be chatbot-driven jobs discovery with no
REST search surface at all; or (b) the real key is resolved by a runtime JS call this static analysis
didn't find (would need Playwright to observe the actual network traffic once the loader executes).
Not resolved further this session -- genuinely needs either Playwright or confirmation from bite that
this listing type has no scrapeable API.

2026-09-21 -- resolved. The b-ite adapter was never the right read path for this board.

AC#1 (is there a v5 postings API behind the loader-v1 'niiid' embed?): no, and it is not a key
problem. cs-assets.b-ite.com/artemed-8/jobs-api/niiid.min.js is 1239 bytes and its whole body is:
resolve api-v5 via window.__$BiteJobsApiLoaderV1$__, call t.createClient({key:""}), then set
window.__$BiteChatbotV1$__ and inject static.b-ite.com/niiid/v1/niiid-recruiting-assistant-v1.min.js
into a #niiid-recruiting-assistant-insert div pointing at https://dot.niiid.io (bot id
07549703-9e92-4e01-88ea-a3ab8a1e23aa). 'niiid' is the BITE recruiting-assistant CHATBOT product, not
a job listing -- so there is no per-tenant key to find and no postings endpoint to call.
Confirmed with the Playwright probe the task asked for (headless chromium, networkidle + 6s, 37
requests): every b-ite request the loader makes is chatbot asset traffic
(loader-v1 -> niiid.min.js -> api-v5.min.js -> niiid-recruiting-assistant-v1.min.js ->
dot.niiid.io/config?bot=... -> avatar_artie.svg / open_chat.svg). ZERO calls to
jobs.b-ite.com/api/v1/postings/search. The previous session's finding (key="" -> 400 'API key is
missing') was not an incomplete reading: there genuinely is nothing to call.

AC#2: the board's real postings are on SmartRecruiters, on that same page. The career page carries
a second widget -- company_code ArtemedSE, filter_locations Feldafing -- and the repo already has a
working adapter for it (crawl_smartrecruiters). The ROOT cause of the 0-yield was in that adapter,
not in bite.py: this page embeds the widget config inside a data-widget HTML ATTRIBUTE, so its JSON
arrives entity-escaped (data-widget="widget({&quot;company_code&quot;: &quot;ArtemedSE&quot;, ...})")
and _smartrecruiters_ident's "company_code" regex matched nothing. Fixed by entity-decoding before
matching (crawlers/vendor_adapters.py:_smartrecruiters_ident), which is the same lesson parse_job_page
already learned for escaped JSON-LD strings on jobs.bezirkskliniken-schwaben.de.
Measured live 2026-09-21: _smartrecruiters_ident returned None on all three Artemed career pages
before the fix and ArtemedSE on all three after. Full ArtemedSE walk = 425 postings, of which 23 are
Feldafing and 8 survive classify (6 pflegefachkraft 'Gesundheits- und Krankenpfleger, Pflegefachfrau/
mann oder Altenpfleger ...', 2 pflegehelfer 'Stationsassistenz').

AC#3: the two other Artemed clinics (16228 Artemed Klinikum Muenchen Sued, 16235 Artemed Fachklinik
Muenchen) run the SAME SmartRecruiters widget (company_code ArtemedSE, filter_locations 'Muenchen
Sued' / 'Muenchen Mitte'), entity-escaped the same way -- and, unlike Feldafing, they carry NO
data-bite-jobs-api-listing mount at all. Both yielded 0 rows before the fix (raw_board_rows, live):
their careers_url has no category subpages, so crawl_smartrecruiters's subpage probe -- which is the
only reason Feldafing scraped through at all -- found nothing and they fell through to a yield-0
crawl_wp_jobs walk. Post-fix both resolve ArtemedSE directly. The two read paths are NOT duplicates:
the b-ite side has no postings whatsoever, so there is exactly one read path, no second one to add.
data/registry/clinics.csv still labelled all three ats_type=bite while the live DB already says
smartrecruiters; corrected in the CSV so a registry push cannot re-break them.

Validation 2026-09-21: full offline suite 1236 passed, 1 skipped, 1195 deselected, 0 failed (.venv/bin/python -m pytest -m "not network" -q, 377s). Targeted: tests/test_vendor_adapters.py 39 passed. Mutation test of the new tests: replacing _html.unescape(html or "") with html or "" gives 2 failed / 37 passed; restoring gives 39 passed.
Files changed: crawlers/vendor_adapters.py (_smartrecruiters_ident entity-decodes first), tests/fixtures/board_samples/klinik_feldafing_stellenangebote_sample.html (new), tests/fixtures/board_samples/README.md, tests/test_vendor_adapters.py (2 tests), data/registry/clinics.csv (ats_type bite -> smartrecruiters for 16228/16235/18813). pflege_jobs/sources/bite.py is deliberately UNCHANGED: its handling of this embed generation was already correct -- it recognises the mount, fetches the bundle, finds no key and says so. There is no key to find.

Live post-fix measurement 2026-09-21 (crawl_smartrecruiters against the three real career pages; per-posting detail fetches stubbed out because they are not what this change touches):
  16228 Artemed Klinikum Muenchen Sued  board rows 0 -> 425, own-site postings 11, nursing 3
  16235 Artemed Fachklinik Muenchen     board rows 0 -> 425, own-site postings  5, nursing 4
  18813 Benedictus Krankenhaus Feldafing board rows 425 -> 425, own-site postings 23, nursing 8
(nursing = survives classify_role with section.job_confirmed_nursing from the posting's own
department label; excluded classes nicht_pflege/ausbildung/werkstudent_praktikum.) 15 nursing
postings across the three Bavarian Artemed sites become reachable, from boards that returned 0 rows
(the two Muenchen ones) or only scraped through by accident (Feldafing, via crawl_smartrecruiters's
category-subpage probe, which the two Muenchen careers_urls have no subpages for).
Prod today, read-only: 16228 has 0 open postings; 18813 has exactly 1, posting_id 10384 'Unsere
Stellenangebote im Pflegedienst' -- a nav page turned into a fake posting by the yield-0
crawl_wp_jobs fallback this fix stops reaching. Six of Feldafing's real nursing postings are already
in the table with clinic_id NULL and four more are attached to 18105 Kloster Diessen; re-linking
those existing rows is prod data repair (tools/reverify_and_clean.py), deliberately not done here.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Answered and fixed. There is no v5 postings API behind the loader-v1 'niiid' embed: that bundle (1239 bytes) resolves api-v5, calls createClient({key:""}) and then injects the BITE recruiting-assistant CHATBOT (static.b-ite.com/niiid/v1, dot.niiid.io, bot 07549703-9e92-4e01-88ea-a3ab8a1e23aa). Proven with the Playwright probe the task asked for: headless chromium, networkidle + 6s, 37 requests, every b-ite call is chatbot asset traffic and ZERO calls to jobs.b-ite.com/api/v1/postings/search. pflege_jobs/sources/bite.py was already correct and is unchanged -- there is no key to find. The board's real postings are on SmartRecruiters (company_code ArtemedSE) on that same page, and the root cause of the 0-yield was in crawl_smartrecruiters, not bite: this page carries the widget config inside a data-widget HTML attribute, so its JSON is entity-escaped and _smartrecruiters_ident's "company_code" regex matched nothing. Fixed by entity-decoding before matching (crawlers/vendor_adapters.py). Measured live: the regex returned None on all three Artemed career pages before and ArtemedSE on all three after; board rows 0 -> 425 for 16228 and 16235, and Feldafing/Muenchen Sued/Muenchen Mitte now expose 23/11/5 own-site postings of which 8/3/4 survive classify -- 15 nursing postings across the three Bavarian Artemed sites. AC#3: the two sibling clinics run the same escaped ArtemedSE widget and carry no b-ite mount at all, and the two read paths are not duplicates because the b-ite side has no postings whatsoever -- there is exactly one read path. data/registry/clinics.csv still said ats_type=bite for all three while the live DB already said smartrecruiters; corrected so a registry push cannot re-break them. Frozen offline as tests/fixtures/board_samples/klinik_feldafing_stellenangebote_sample.html plus 2 tests in tests/test_vendor_adapters.py, mutation-tested (removing the unescape gives 2 failed / 37 passed, restoring gives 39 passed). Full offline suite: 1236 passed, 1 skipped, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
