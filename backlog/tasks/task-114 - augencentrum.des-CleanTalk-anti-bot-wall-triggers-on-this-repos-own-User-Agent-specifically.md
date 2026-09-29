---
id: TASK-114
title: >-
  augencentrum.de's CleanTalk anti-bot wall triggers on this repo's own
  User-Agent specifically
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 17:13'
updated_date: '2026-09-23 15:15'
labels: []
dependencies: []
ordinal: 114000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinic 16307 (AugenCentrum Rosenheim), board https://www.augencentrum.de/ueber-uns/karriere/. Every request with this repo's UA (crawlers.vendor_adapters.UA, 'Mozilla/5.0 (X11; Linux x86_64) ... Chrome/125.0.0.0 ...') gets CleanTalk's 'Anti-Crawler-Schutz' JS-cookie-challenge page (a WordPress plugin, cleantalk-spam-protect) instead of the real page. Confirmed live: swapping to a plain Windows-Chrome UA, or even bare curl's own default UA, gets the REAL page first try (no cookie dance needed) -- so this is not a genuine 'needs a browser' JS-rendering case, just a UA-specific block, and the real page has real content once past it: at least 1 nursing posting ('Pflegefachkraft (m/w/d)') plus an MFA posting. crawlers.vendor_adapters.get()/H is one shared module-level constant used by every board in this vendor family -- changing it globally risks fingerprint/rate-limit fallout across every other board, so this needs a per-domain override mechanism, not a blanket UA swap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A per-domain (or per-board) User-Agent override mechanism is added to crawlers.vendor_adapters.get() (or a narrowly-scoped equivalent), used ONLY for augencentrum.de, leaving the shared UA/H constant untouched for every other board
- [ ] #2 Verified live: crawl_wp_jobs on this board's careers_url returns the real postings (Pflegefachkraft, MFA, ...) with the override in place
- [x] #3 A red-green test proves the override is what unblocks it (e.g. mock two responses keyed by header, or document why a live-only proof was used instead)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Re-verify the CleanTalk block live before touching code (curl + Python requests, repo UA vs Windows-Chrome UA vs curl default).
2. Add UA_OVERRIDE dict + _headers_for(url) helper in crawlers/vendor_adapters.py, keyed by netloc, used only inside get(); H/UA stay untouched.
3. Wire _headers_for(u) into both requests calls inside get() (initial + meta-refresh follow), replacing the hardcoded headers=H.
4. Verify live: _headers_for returns the override only for augencentrum.de hosts, default H for everything else; get() against the real board returns 200 with real content.
5. Write a mocked red-green test (UA-keyed fake session) proving get() sends the override UA to augencentrum.de and the default UA everywhere else; mutation-test by reverting the wiring in a /tmp copy, confirm red, restore via /tmp diff -q, confirm green.
6. Run the full tests/test_vendor_adapters.py file.
7. Record findings honestly against each AC, including the newly discovered discrepancy: crawl_wp_jobs still returns 0 rows for this board even with the override, for an unrelated reason.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented AC#1: added UA_OVERRIDE dict + _headers_for(url) helper in crawlers/vendor_adapters.py, keyed by netloc (exact host or subdomain suffix match), wired into both requests calls inside get() (initial fetch + meta-refresh follow). H/UA stay the untouched shared default for every other board -- confirmed via a test asserting get() sends UA exactly once, unchanged, for a non-augencentrum host.

Live re-verification (2026-09-23) found the tasks own premise is stale: the CleanTalk block described from 2026-09-22 does not reproduce anymore. curl with its own default UA, curl with this repos exact UA, curl with a Windows-Chrome UA, and this repos own get() (Python requests, unmodified UA) all return byte-identical (md5-matched) 200 responses carrying the real page -- including "Pflegefachkraft (m/w/d)" and an MFA posting -- with zero CleanTalk challenge/anti-crawler markup in any of them, across 6 rapid repeat requests too (no rate-based block either). So the block AC#1s mechanism targets was real on 2026-09-22 but is not live today; the override itself is correctly implemented and inert (harmless, ready for if/when the block returns).

AC#2 ("verified live: crawl_wp_jobs ... returns the real postings ... with the override in place") does NOT hold, but not because of anything this tasks mechanism controls: crawl_wp_jobs(c) with careers_url=https://www.augencentrum.de/ueber-uns/karriere/ returns 0 rows regardless of UA/override, because this boards real posting shape -- inline `<div class="single_job"><h2>title</h2><div class="job__content">...` directly on the career page, no sitemap entry, no separate detail-page link -- matches none of the 7 existing inline-shape extractors crawl_wp_jobs already tries (verified by calling each of _faqpage_job_rows/_faq_accordion_job_rows/_inline_heading_job_rows/_title_only_job_rows/_bootstrap_panel_job_rows/_dan_bewerbungen_job_rows/_elementor_toggle_job_rows directly against the real live response: all 7 returned 0). This is a separate, pre-existing gap, unrelated to UA blocking and outside a UA-override mechanisms reach -- filed as TASK-135, left unchecked here rather than silently expanding this tasks scope to also write a new extractor.

AC#3: mocked red-green test added (tests/test_vendor_adapters.py: test_get_sends_the_override_ua_only_for_augencentrum_de, test_get_leaves_the_shared_default_ua_untouched_for_every_other_host) -- a live-only proof isnt usable/reliable per the above (the real block doesnt reproduce right now), so a UA-keyed fake session stands in, documented inline in the test docstring. Mutation-tested: reverted the headers=_headers_for(u/nxt) wiring in a /tmp copy back to headers=H, confirmed both new tests go red (the override test fails with the exact challenge-vs-real assertion), restored the real file from the untouched /tmp backup, confirmed diff -q byte-identical, reran green.

Full tests/test_vendor_adapters.py: 78 passed, 0 failed (was 76 before the 2 new tests). No registry write needed/attempted -- Supabase still unreachable, not touched.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Per-domain UA override mechanism added to crawlers.vendor_adapters.get() (UA_OVERRIDE dict + _headers_for(), scoped to augencentrum.de only, shared H/UA untouched for every other board) -- AC#1 met. AC#3 met via a mocked, mutation-tested red-green test (live proof unavailable: re-verified live 2026-09-23 that the CleanTalk block this task targets no longer reproduces on any UA). AC#2 not met: crawl_wp_jobs still returns 0 rows for this board, but for an unrelated, newly discovered reason (its inline single_job/h2 posting shape matches none of the existing extractors, not a UA block) -- filed as TASK-135, left unchecked here rather than scope-creeping this task into writing a new extractor. tests/test_vendor_adapters.py: 78 passed, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
