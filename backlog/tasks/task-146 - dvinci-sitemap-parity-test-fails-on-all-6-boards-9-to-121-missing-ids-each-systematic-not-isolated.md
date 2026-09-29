---
id: TASK-146
title: >-
  dvinci sitemap-parity test fails on all 6 boards -- 9 to 121 missing ids each,
  systematic not isolated
status: Done
assignee: []
created_date: '2026-09-23 23:47'
updated_date: '2026-09-23 23:50'
labels: []
dependencies: []
ordinal: 146000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Surfaced during a broader regression sweep (tests/ -k "matcher or registry or vendor or crawl") run after unrelated TASK-128 rexx/firecrawl work -- dvinci code itself was not touched in that work. tests/test_completeness_dvinci.py::test_sitemap_ids_match_crawled_rows (a live-network test, pytest.mark.network) compares each dvinci tenant's own sitemap.xml posting ids against what crawl_dvinci() actually returns. All 6 dvinci boards in the registry failed, each missing a substantial, non-trivial count of sitemap ids: ukw.de 79, romed-jobs.de 76, sozialstiftung-bamberg.de 121, klinikum-fuerth.de 67, klinikum-neumarkt.de 46, salus-klinik.de 9. Failing on every board rather than one suggests a systematic cause (a shared crawl_dvinci/pagination regression, or a vendor-side sitemap/board format change across all dvinci tenants) rather than one site's local quirk. Some missing ids are suspiciously low/short (e.g. 104, 195, 44, 100) next to typical 5-digit live ids, which could mean the sitemap lists old/closed postings crawl_dvinci correctly excludes -- i.e. the test's own oracle may be wrong, not the crawler. Ivan's steer (2026-09-23): we probably don't need this test as-is, but the underlying site/vendor change is worth understanding before deciding that.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Root cause identified for the missing ids on at least 2 of the 6 boards: either a real crawl_dvinci completeness gap, a vendor-side board/sitemap format change, or the sitemap listing ids the live board itself no longer shows (stale/closed postings) -- with evidence, not a guess
- [x] #2 A decision on test_sitemap_ids_match_crawled_rows: keep it (fixed to the real cause), loosen it (e.g. tolerate closed-posting ids), or remove it as testing the wrong oracle -- documented with why
- [x] #3 If a real crawl_dvinci gap is found, it is fixed and mutation-tested; if not, no adapter code changes
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Root cause: not a crawl_dvinci gap, a test-oracle bug. tests/test_completeness_dvinci.py's own
_ID_RX = re.compile(r"/jobs/(\d+)/") required a trailing slash right after the numeric id. That
matched sitemap.xml's <loc> entries fine (they keep the /<slug>), but crawl_dvinci's own
parse_dvinci() intentionally strips that trailing slug (2026-09-22 dedup fix, so one job is never
stored twice under two URL shapes) -- so every crawled row's source_url ends in the bare id with no
following slash, and the test's own regex silently matched ZERO of them. Confirmed live on all 6
dvinci boards by comparing against a slash-agnostic regex: ukw.de 79/79 ids matched, romed-jobs.de
76/76, sozialstiftung-bamberg.de 121/121, klinikum-fuerth.de 67/67, klinikum-neumarkt.de 46/46,
salus-klinik.de 9/9 -- zero real completeness gaps anywhere, list.json (crawl_dvinci's only data
source, confirmed no pagination exists to walk) already contains every sitemap id.

Fix: _ID_RX relaxed to r"/jobs/(\d+)" (no trailing-slash requirement), matches both the sitemap's
slugged URLs and the crawler's normalized bare-id URLs. All 6 parametrized cases pass. Mutation-tested:
reverted to the trailing-slash regex, confirmed all 6 red again with the exact original failure
counts, restored from a /tmp copy (byte-identical via diff -q), re-confirmed green.

AC#2 (keep/loosen/remove the test): kept, fixed to match crawl_dvinci's own actual (correct) URL
shape -- it's a real, useful oracle (an independent, vendor-native source of "what ids exist"), the
regex was just stale against a URL-normalization change made the same week. No adapter code changed
(AC#3): crawl_dvinci was already complete, nothing to fix there.
<!-- SECTION:FINAL_SUMMARY:END -->
