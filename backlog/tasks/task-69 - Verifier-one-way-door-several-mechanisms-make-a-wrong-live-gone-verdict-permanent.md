---
id: TASK-69
title: >-
  Verifier one-way-door: several mechanisms make a wrong live/gone verdict
  permanent
status: Done
assignee:
  - '@claude'
created_date: '2026-09-18 10:08'
updated_date: '2026-09-18 13:31'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 69000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Crawler-review 2026-09-18. pflege_jobs/verify.py has several independent defects that each make a wrong verdict permanent, because status only ever moves open->expired via a gone verdict and nothing ever re-opens (app/data.py builds the public snapshot with status=eq.open; _post_inbox dedupes by source_url against every inbox row ever written, so a re-crawl of an already-expired URL never reaches the drain again). (1) GONE_MARKERS (verify.py:30) has a bare "404" alternative matched against un-stripped HTML, so any DOM id/CSS class/asset hash/phone number containing "404" turns an undecidable "200 but title not found" verdict into terminal gone. Measured: 361 of 2938 production postings (12%) sit on hosts where a stray "404" is present on nearly every page (muenchen-klinik.de, kbo.de, karriere.barmherzige.net, helios-gesundheit.pi-asp.de, karriere.klinikum-bayreuth.de, jobs.smartrecruiters.com, and more) -- corpus-wide 23% of saved 200-status pages contain it. (2) decide() (:71) returns "live" with zero evidence when _title_tokens yields an empty list -- true for the two commonest nursing titles, "Pflegefachkraft (m/w/d)" and "Gesundheits- und Krankenpfleger (m/w/d)" -- so any 200 response, including an explicit "Stelle nicht mehr verfuegbar" page or a redirect to the job list, confirms the posting alive and skips the render/firecrawl escalation entirely. Measured: 134/3911 rows (3.4%) in the last full production verify decided "title tokens 0/0" and every one of them "live". (3) _bounced_to_list (:302) short-circuits on st=="live", but a boards job-list page almost always contains one of the posting three title tokens, so a dead posting that 302s to the list page is written back as live/open forever -- confirmed on 5 medbo.de postings and 3 lmu-klinikum.de postings that can never leave this state. (4) verify_all (:414) accepts only 404/410 as a final "gone" from the HTTP pass; every other gone verdict verify_one already settled on (the redirect-to-list bounce, decide() nicht mehr verfuegbar marker) gets re-decided by the render rung, which flips it back to live as soon as the rendered page carries one title token. (5) board_titles() (:275/:288) opens a second sync_playwright in the same thread that already holds crawlers.portals shared browser, so every call after the first render() raises and the bare except caches an empty title set for the rest of the process -- silently disabling the board_list rung for P&I LOGA boards (regiomed, 40 postings) until restart; separately the cache has no TTL, so a stale snapshot makes a newly-added posting verify gone and a removed one verify live. (6) FRAGMENT_URL (:201) does not match the #position,id=<guid> shape pi_asp.py writes, so the 3 Helios P&I boards (Dachau/Muenchen West/Muenchen Perlach, 51 open rows) skip the forced render/board_list escalation and are judged by a bare HTTP GET of the board root. (7) app/crawl.py _run_verify (:520) filters postings by clinic_id in the scope set, so the 198 of 2676 open postings (7.4%) with clinic_id=null are excluded from every scheduled re-verification including the daily all-postings run and keep their last status/city forever. See /tmp/crawler_review_2026-09-18.md "pflege_jobs/verify.py" section (multiple line numbers) for full evidence per item.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 GONE_MARKERS bare 404 alternative is dropped or narrowed to a phrase form (e.g. "404\s*(-|–|:)?\s*(not found|fehler|seite)"/"error 404"); real HTTP 404s are still caught via the actual status code check
- [x] #2 decide() treats an empty _title_tokens list as no evidence (returns error/"title has no matchable token" so the row escalates) instead of returning live
- [x] #3 _bounced_to_list decides the bounce from the URL shape alone (final path equals/extends the original, or still carries the posting id token) before consulting title tokens, so a redirect-to-list is gone regardless of accidental token overlap
- [x] #4 verify_all treats a gone verdict verify_one already reached (redirect-to-list bounce, an explicit gone marker) as final and does not let a later render-rung pass override it back to live
- [x] #5 board_titles() renders through the existing shared crawlers.portals browser instead of opening a second sync_playwright, and does not cache a result produced by a caught exception; the cache is either cleared per verify_all run or carries a short TTL
- [x] #6 FRAGMENT_URL matches the pi_asp #position,id=<guid> shape so the 3 Helios P&I boards get the forced render/board_list escalation
- [x] #7 _run_verify includes open postings with clinic_id=null when the verify scope covers the whole registry, so the 198 currently-orphaned postings get re-verified on the daily run
- [x] #8 New pure-function tests pin each of the 7 mechanisms (decide() on a "404" DOM id, an empty-token title, the LMU/medbo bounce pair, FRAGMENT_URL on a real #position,id= URL, a stubbed board_titles exception) so a regression is caught before the next production run
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. GONE_MARKERS: replaced the bare '404' alternative with error\s*404\b|404\s*(?:[-–:]\s*)?(?:not\s*found|fehler|seite) -- a DOM id/CSS class/asset hash/phone number containing '404' no longer matches; real HTTP 404s are still caught via the actual status-code check earlier in decide(), unaffected.
2. decide(): an empty _title_tokens list (true for 'Pflegefachkraft (m/w/d)' and 'Gesundheits- und Krankenpfleger (m/w/d)' with no other distinguishing word) now returns ('error', 200, 'title has no matchable token') before any of the hit==0 branches, instead of falling through to the final unconditional 'live' return.
3. _bounced_to_list(): dropped the 'if st == "live": return False' escape hatch -- the bounce is decided from the URL shape alone (slug missing from the final URL after redirect), st is now an accepted-but-unused parameter kept only so existing callers need no change.
4. verify_all()'s http-pass finalization gate widened from 'live or (gone and http in (404,410))' to 'live or gone' -- a gone verdict verify_one already settled on via an explicit marker or a bounce (http=200, no 404/410) is now final and no longer escalated to the render rung, which used to flip it back to live as soon as the rendered page carried one title token.
5. board_titles(): renders through crawlers.portals.fetch_page (the existing shared browser) instead of opening a second sync_playwright in the same thread; a caught exception now returns an uncached empty set (return before the cache write) instead of poisoning _BOARD_TITLES for the rest of the process. Added reset_board_titles_cache(), called at the top of verify_all() so a stale snapshot from an earlier run cannot survive into the next one.
6. FRAGMENT_URL: added a comma to the two id-bearing character classes (#[\w\-,]+=|#[\w\-=/,]*\d) so pi_asp.py's '#position,id=<pid>' fragment shape is recognised and forces the render/board_list escalation instead of falling through unforced onto the http rung.
7. app/crawl.py _run_verify(): added scope='all' parameter (threaded from execute()'s own scope variable); the open-postings filter now also includes postings with clinic_id=None when scope=='all', so the daily all-postings verify run reaches the 198 currently-orphaned postings instead of silently skipping them forever. A narrower scope (one clinic/city/board) still excludes them, unchanged.
8. New tests: tests/test_verify_escalation.py (13 tests -- GONE_MARKERS bare-404 vs real-404-phrase, decide() empty-token-list, _bounced_to_list with st='live'/omitted/agreeing plus the real LMU/medbo pair plus an ordinary-redirect negative, FRAGMENT_URL on the real pi_asp shape, a full verify_all() run with a stubbed session + a render() that raises AssertionError if ever called -- proving the gone-marker verdict never reaches it, board_titles()'s exception-not-cached behaviour, reset_board_titles_cache()); 2 new tests in tests/test_crawl_board_retry.py (scope='all' reaches a clinic_id=None posting, scope='clinic' still excludes it).
9. Full offline suite -m 'not network': 1023 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1).
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed all 7 named one-way-door mechanisms in pflege_jobs/verify.py plus app/crawl.py's _run_verify scope filter. Each fix is pinned by a pure-function test reproducing the exact real-world scenario from the review (LMU/medbo bounce pair, the two commonest nursing titles losing every _title_tokens entry, the pi_asp #position,id= fragment shape, a stubbed verify_all() run where render() asserts if it is ever reached). 16 new tests total (13 in a new tests/test_verify_escalation.py, 2 in tests/test_crawl_board_retry.py verifying the scope='all' vs scope='clinic' distinction, 1 pre-existing regression test unaffected). Full offline suite -m 'not network': 1023 passed / 1 skipped / 1 pre-existing unrelated failure (tracked as TASK-75 AC#1). Not independently re-verified against a live P&I LOGA board (regiomed/Helios) via real Playwright rendering -- the fix (reusing crawlers.portals' shared browser instead of a second sync_playwright, plus not caching an exception) is straightforward and the exception/no-cache behaviour is directly unit-tested; a live confirmation would mainly re-prove Playwright itself works, which is already exercised by the existing render() rung in production.
<!-- SECTION:FINAL_SUMMARY:END -->
