---
id: TASK-64
title: wp_jobs regressions since 2026-09-11 silently empty several boards
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:06'
updated_date: '2026-09-18 11:13'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 64000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18. Four distinct regressions in crawlers/vendor_adapters.py, all landed around commit 7471cae (2026-09-11) / 532de0f, all reported as success (0 crawl_issues). (1) _listing_page_key (:782) keys candidate URLs by (netloc, path) only, dropping the query string, so any board that identifies postings by a query param collapses onto one key: crawl_wp_jobs then dedupes to a single fetch. Confirmed: Klinikum Wuerzburg Mitte (66301, 647 beds) had 23 rows on 2026-09-09, down to 1 on 2026-09-13 and 2026-09-17 — its 53 live postings sit behind .../uebersicht-aller-stellen/details/?job=<uuid>; DZKJR Garmisch-Partenkirchen (18002) and Koenig-Ludwig-Haus Wuerzburg (66305, 14 postings) hit the same collapse. (2) _faqpage_job_rows (:975) accepts any FAQPage Question as a posting with no job-shape gate, and crawl_wp_jobs returns early on any such rows, so a page with an ordinary application FAQ skips the sitemap/career-page walk entirely: confirmed on karriere.klinikum-altmuehlfranken.de (57701 Weissenburg 190 beds + 57705 Gunzenhausen 210 beds), 6 real postings (3 nursing) lost every run since commit 532de0f (present in run_67.jsonl 2026-09-08, gone by run_83.jsonl 2026-09-12). (3) The four listing-page-only helpers (_faqpage_job_rows:811, _faq_accordion_job_rows:846, _title_only_job_rows:907, _bootstrap_panel_job_rows:934) stamp every posting of a board with the listing page own URL, so _post_inbox source_url dedupe keeps exactly one posting per board: confirmed on klinik-steger.de (5->1), waldhausklinik.de (11->1), klinik-bad-trissl.de (7->1), klinik-wirsberg.de (2->1). (4) parse_job_page (:504) trusts the JSON-LD url field verbatim as source_url; on komm-ins-klinikland.de (Klinik Kitzinger Land, 67501, 200 beds) every posting JSON-LD names the site root, so dedupe keeps one row and it is permanently the lowest-value posting (4 nursing postings, including "Kinderkrankenpfleger", lost every run, never rediscovered because the existing-URL check matches the root on every later run too). See /tmp/crawler_review_2026-09-18.md "crawlers/vendor_adapters.py" :782/:975/:811/:504 for full evidence and the per-mechanism fixes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 _listing_page_key includes a normalised query string (e.g. sorted parse_qsl) in its key so postings distinguished only by a query parameter no longer collide; Klinikum Wuerzburg Mitte and DZKJR Garmisch-Partenkirchen recover their full posting counts on the next adapter run
- [x] #2 _faqpage_job_rows only accepts a Question whose text carries a gender marker (m/w/d or equivalent) as a posting, and crawl_wp_jobs merges its rows into the normal walk instead of returning early, so karriere.klinikum-altmuehlfranken.de recovers all 6 real postings including the sitemap-discovered ones
- [x] #3 The four listing-page-only helpers give each posting a distinct URL (e.g. the listing URL plus a digit-free slug fragment) instead of the shared listing URL, so klinik-steger.de/waldhausklinik.de/klinik-bad-trissl.de/klinik-wirsberg.de each keep all their postings after dedupe
- [x] #4 parse_job_page accepts a JSON-LD url only when it is on the same host and its path is not the site root, otherwise keeps the fetched detail URL, so Klinik Kitzinger Land postings get distinct source_urls and its lost nursing postings reappear
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. crawlers/vendor_adapters.py: added _fetch_dedupe_key(u) (netloc, path, sorted query) and switched _wp_job_rows' own fetch/redirect-bounce 'seen' dedup to it, leaving _listing_page_key (query-blind, used only for the separate not_a_job 'is this the listing page itself' test) untouched -- postings distinguished only by a query parameter (e.g. .../details/?job=<uuid>) no longer collapse onto one fetch.
2. _faqpage_job_rows now requires GENDER.search(title) before accepting a FAQPage Question as a posting (an application FAQ like 'Wie bewerbe ich mich?' shares the identical FAQPage shape and was previously accepted with no job-shape gate at all).
3. crawl_wp_jobs no longer returns early when the four listing-page-only helpers (_faqpage_job_rows, _faq_accordion_job_rows, _title_only_job_rows, _bootstrap_panel_job_rows) yield rows -- they are merged into out/fetched/seen (created earlier in the function for this) and the sitemap/section/career-page-link walks still run afterward.
4. Added _synthetic_job_url(page_url, title) (page_url + '#' + a digit-free title slug) and used it for the row/source_url of all four listing-page-only helpers -- they previously all shared the one page_url as source_url, so app/crawl.py _post_inbox's dedupe-by-source_url collapsed every posting on such a board down to one row.
5. parse_job_page's JSON-LD branch now only trusts n.get('url') when it is on the same host as the fetched page and its path is not the bare site root; otherwise it keeps the URL actually fetched -- a board whose JSON-LD template names the site root on every posting (komm-ins-klinikland.de) no longer collapses every posting's source_url onto that one root URL.
6. Added a NOT_JOB_PATH exclusion for '?kategorie='/'?category=' query keys -- a department-filtered overview page living under the same '/stellenanzeigen/'-style folder as real detail pages otherwise substring-matched JOB_PATH and got mis-parsed as a posting under its own generic page title.
7. Widened GENDER to also accept the colon/asterisk gender-neutral suffix ('Pfleger:in', 'Mitarbeiter*in'), additive only -- discovered live while verifying fix #2 against klinik-steger.de (the review's own named FAQPage example), which had since switched from '(m/w/d)' to this style; without the widening, fix #2's new gender-gate would have silently dropped 100% of that board's real postings.
8. Live read-only re-verification against the current registry (all 4 boards fetched with real HTTP, no writes): DZKJR Garmisch-Partenkirchen (18002) 6/6 distinct real rows (was the FAQ/oracle-fallback example); Klinikum Altmuehlfranken Weissenburg (57701, oracle->wp_jobs fallback) 6/6 distinct real rows incl. 3 nursing titles (fix #2/#3's named example); Klinik Kitzinger Land (67501) 11/11 distinct real rows incl. the exact nursing titles the review named as lost (fix #5's named example); klinik-steger.de (56409) 3/3 distinct real rows incl. 2 nursing titles (fix #2's regression risk from #7, now recovered). Klinikum Wuerzburg Mitte (66301, fix #1's named example) does NOT recover its 53 postings -- investigated live and found its real .../details/?job=<uuid> detail pages are a JS-rendered SPA (no JobPosting JSON-LD, no gender-marked text anywhere in the static HTML at all); the dedupe-key fix is still correct and necessary (verified independently via a synthetic query-string test and via DZKJR/Kitzingen, which ARE query-string-distinguished and DO recover), but this specific board needs Firecrawl/Playwright rendering, unrelated to the dedupe mechanism -- not silently claimed fixed, see notes.
9. tests/test_completeness_wp_jobs.py: 11 new tests covering all of the above (query-string dedupe, FAQ-shape merge + gender gate, the 4 helpers' distinct synthetic URLs incl. digit/'='-free fragments, JSON-LD url same-host/non-root guard, GENDER colon-form, category-filter exclusion).
10. Full offline suite -m 'not network': 997 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Klinikum Wuerzburg Mitte (clinic 66301) needs a registry/routing fix, not an adapter fix: its careers_url resolves to a JS-rendered SPA with no server-rendered job content at all (confirmed live: the /details/?job=<uuid> detail page's static HTML has no JSON-LD, no gender-marked text, nothing but an a11y h1 label). It should likely be routed to Firecrawl (fetch=firecrawl / mode=auto) instead of the default wp_jobs adapter. Left as a new, separate, small finding rather than silently claimed fixed under this task -- worth a follow-up note under TASK-76 (uncovered areas) or a new one-line registry fix task if the user wants it filed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed 3 of the 4 named wp_jobs regressions plus 2 more defects found while verifying live. AC#2 (FAQ-shape gender-gate + merge instead of early-return), AC#3 (4 listing-page-only helpers get distinct synthetic URLs), AC#4 (parse_job_page's JSON-LD url guarded to same-host/non-root) are all verified with live, read-only re-crawls against the current registry: DZKJR Garmisch-Partenkirchen 6/6 rows, Klinikum Altmuehlfranken Weissenburg 6/6 rows incl. 3 nursing, Klinik Kitzinger Land 11/11 rows incl. the exact lost nursing titles the review named, klinik-steger.de 3/3 rows incl. 2 nursing. Two additional real defects found and fixed during verification, not in the original AC list: a NOT_JOB_PATH exclusion for '?kategorie='/'?category=' filter query keys (a department-filtered overview page was substring-matching JOB_PATH and getting mis-parsed as a fake posting), and GENDER widened to accept the colon/asterisk gender-neutral suffix ('Pfleger:in') alongside '(m/w/d)' -- klinik-steger.de had switched styles since the review ran, and without this the new FAQ gender-gate (AC#2) would have silently dropped 100% of that board's real postings. AC#1 (the _fetch_dedupe_key fix itself) is implemented and independently proven correct -- a synthetic query-string-only test collapses to 2 rows not 1, and it is the exact mechanism that recovers DZKJR/Kitzingen/Steger above -- but its own named example, Klinikum Wuerzburg Mitte (66301), does NOT recover its posting count: investigated live and found its real detail pages are a JS-rendered SPA with zero server-side job content, a routing/rendering gap unrelated to the dedupe key. Left AC#1 unchecked rather than overclaim; noted the Wuerzburg Mitte finding separately for follow-up (likely belongs on Firecrawl routing, not this adapter). 11 new tests in tests/test_completeness_wp_jobs.py. Full offline suite -m 'not network': 997 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:FINAL_SUMMARY:END -->
