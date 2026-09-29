---
id: TASK-135
title: >-
  augencentrum.de: crawl_wp_jobs still returns 0 rows past the (now-stale)
  CleanTalk UA block -- its inline 'single_job' listing shape matches no
  existing extractor
status: Done
assignee:
  - '@ivan-agent'
created_date: '2026-09-23 15:15'
updated_date: '2026-09-24 11:09'
labels: []
dependencies: []
references:
  - crawlers/vendor_adapters.py
  - TASK-114
priority: low
type: bug
ordinal: 135000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-114 follow-up, found while implementing/verifying TASK-114 live (2026-09-23). TASK-114s premise -- CleanTalk serving an anti-crawler JS-cookie-challenge page to this repos own UA -- does NOT reproduce anymore: re-verified live with curl default UA, this repos exact UA, and a Windows-Chrome UA, all three return byte-identical 200 responses (md5-matched) carrying the real page, including "Pflegefachkraft (m/w/d)" and an MFA posting inline. So the 2026-09-22 block was real but is not currently live; TASK-114s per-domain UA-override mechanism is implemented, correct, and harmless, but is not what stands between this board and real rows today.\n\nRunning crawl_wp_jobs(c) with careers_url=https://www.augencentrum.de/ueber-uns/karriere/ (any UA) returns 0 rows. find_job_urls finds 0 job links in the sitemap. None of the 7 inline-shape extractors crawl_wp_jobs already tries on cu_resp match either (_faqpage_job_rows, _faq_accordion_job_rows, _inline_heading_job_rows, _title_only_job_rows, _bootstrap_panel_job_rows, _dan_bewerbungen_job_rows, _elementor_toggle_job_rows -- all returned 0 in a direct call). The boards real shape, confirmed live: postings sit directly on the career page itself, each as `<div class="single_job"><h2>Pflegefachkraft (m/w/d)</h2><div class="job__content">...</div></div>`, with no separate detail-page link and no sitemap entry -- a shape none of the existing helpers parse.\n\nNot yet investigated: whether this single_job/h2 shape is unique to this one site or a recognizable pattern (a specific WordPress theme/plugin) also present on other zero-yield wp_jobs boards -- worth a quick grep across other zero-yield boards HTML before deciding a narrow vs. generalized extractor.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 crawl_wp_jobs recognizes this boards inline single_job/h2 posting shape and returns real rows for augencentrum.de (at least the Pflegefachkraft and MFA postings), without needing a separate detail-page fetch
- [x] #2 Investigated whether the single_job/h2 shape is a one-off or shared by other currently zero-yield wp_jobs boards, before deciding whether the new extractor should be narrowly scoped to this site or generalized
- [x] #3 Red-green test against a frozen fixture of the real captured page (not a live call in the test)
- [x] #4 Full tests/test_vendor_adapters.py run stays green, no regression to the other inline-shape extractors it already tries first
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Fetch live augencentrum.de/ueber-uns/karriere/ (curl) to confirm current single_job/h2/job__content shape.
2. Read crawlers/vendor_adapters.py's existing 7-8 inline-shape single-site extractors to match style exactly.
3. Write _single_job_job_rows(cu_resp, c, host), host-gated SINGLE_JOB_SITES={"augencentrum.de"}, wire into crawl_wp_jobs's try-in-order faq_rows chain (last).
4. AC#2: grep data/app.sqlite crawl_issues for wp_jobs/self_hosted kind=empty board_urls, fetch ~23 of them live, grep for class=single_job -> zero matches elsewhere -> scope narrowly (host-gated), not generalized.
5. AC#3: save a redacted, byte-order-preserved slice of the real live page as tests/fixtures/board_samples/augencentrum_karriere_sample.html; add test_single_job_shape_reads_both_postings_directly_off_the_career_page to tests/test_completeness_wp_jobs.py.
6. Mutation-test: break SINGLE_JOB_TITLE_RX class name, confirm test RED, restore from /tmp copy, confirm GREEN, diff -q byte-identical.
7. AC#4: run full tests/test_vendor_adapters.py + tests/test_completeness_wp_jobs.py, confirm green.
8. Live spot-check clinic 16307 via crawl_wp_jobs directly against the real careers_url.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Fix: crawlers/vendor_adapters.py -- new host-gated extractor _single_job_job_rows (SINGLE_JOB_SITES={"augencentrum.de"}), wired as the last alternative in crawl_wp_jobs's faq_rows try-in-order chain. Parses <div class="single_job"><h2>title</h2><div class="job__content">body</div></div>, same pattern conventions as the existing 8 single-site extractors (SITES-set gate, GENDER-gated title, lazy body regex to the next </div></div>).

AC#1 (live extraction): confirmed live 2026-09-24 -- curl-fetched the real page (200, 73283 bytes), found the exact single_job/h2/job__content shape for both "MFA (m/w/d)" and "Pflegefachkraft (m/w/d)". Wrote the extractor against that captured HTML. Live spot-check via `va.crawl_wp_jobs({"clinic_id":"16307",...,"careers_url":"https://www.augencentrum.de/ueber-uns/karriere/","ats_type":"self_hosted"})` (registry row read live via app.config.rest_get, ats_type='self_hosted' confirmed) returned exactly 2 rows: "MFA (m/w/d)" and "Pflegefachkraft (m/w/d)", each with its own synthetic #-fragment url and a real description ("Ihre Aufgaben Unterstützung beim Aufbau unserer Station..." / "Gesundheits- und Krankenpfleger/in..." for the nursing one).

Note: the CleanTalk block from TASK-114 IS still live/intermittent -- mid-session the same URL flipped to 403 (JS-cookie-challenge page) for curl AND requests with every UA tried (default, this repo's UA, Windows-Chrome UA), then cleared back to 200 on its own ~3min later (polled every 30s). Not a fix regression: crawl_wp_jobs's own UA_OVERRIDE (TASK-114) already sends the Windows-Chrome UA to this host, same one that got the initial and final 200s. The final live spot-check above ran after the block cleared and got real rows.

AC#2 (scope): queried data/app.sqlite crawl_issues for vendor in (wp_jobs, self_hosted) kind=empty, live-fetched 23 of those distinct board_urls (2026-09-24) and grepped each for `class="single_job"` and `job__content` -- zero matches on every other board. augencentrum.de's theme is "augencentrumrosenheim" (its own body class, a bespoke in-house theme, not a shared WP plugin/vendor fingerprint) -- confirmed one-off, scoped narrowly (host-gated SINGLE_JOB_SITES set) like the other 6 single-site extractors above it, not generalized.

AC#3 (frozen fixture, red-green): saved a real, contiguous, byte-order-preserved slice of the live page as tests/fixtures/board_samples/augencentrum_karriere_sample.html (HR contact name/phone/email redacted per this dir's README convention), documented in the README table. Added test_single_job_shape_reads_both_postings_directly_off_the_career_page to tests/test_completeness_wp_jobs.py -- asserts both titles in order, nursing description content, distinct per-posting urls. Mutation-tested: cp'd vendor_adapters.py to /tmp/vendor_adapters.py.fixed, broke SINGLE_JOB_TITLE_RX's class string (single_job -> single_jobxxx), reran the test -> RED (AssertionError, titles==[]). Restored the file by copying FROM the /tmp copy back, reran -> GREEN, `diff -q` against the /tmp copy confirmed byte-identical restore.

AC#4 (no regression): `.venv/bin/python -m pytest tests/test_vendor_adapters.py tests/test_completeness_wp_jobs.py -q` -> 121 passed (both before and after the mutation-test restore). New extractor is host-gated on "augencentrum.de" like its 6 SITES-gated siblings (_inline_heading_job_rows, _title_only_job_rows, _bootstrap_panel_job_rows, _dan_bewerbungen_job_rows, _elementor_toggle_job_rows, _divi_toggle_job_rows) -- mutually exclusive by domain, so trying it last in the chain cannot shadow any other board's own extractor.

Registry: left ats_type='self_hosted' as-is per the task's own guidance (both 'self_hosted' and 'wp_jobs' route to crawl_wp_jobs per crawlers/routing.py's FALLBACK_VENDORS/VENDOR_MAP, so the fix applies regardless of the stored label; no registry write needed or made).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added crawlers/vendor_adapters.py:_single_job_job_rows (host-gated on augencentrum.de) to crawl_wp_jobs's inline-shape chain, recognizing <div class="single_job"><h2>title</h2><div class="job__content">...</div></div>. Verified against a frozen fixture (tests/fixtures/board_samples/augencentrum_karriere_sample.html, real captured page) with a new red-green test in tests/test_completeness_wp_jobs.py, mutation-tested (RED on a broken regex, byte-identical restore, GREEN). Grepped 23 other currently zero-yield wp_jobs/self_hosted boards live -- no other board shares this class name, so scoped narrowly to this one site, matching the existing single-site extractor convention. Full tests/test_vendor_adapters.py + tests/test_completeness_wp_jobs.py suite: 121 passed, no regression. Live spot-check: crawl_wp_jobs against clinic 16307's real careers_url now returns both "MFA (m/w/d)" and "Pflegefachkraft (m/w/d)" with real descriptions (previously 0 rows). Registry ats_type left as 'self_hosted' -- both self_hosted and wp_jobs route to crawl_wp_jobs, so no registry write was needed.
<!-- SECTION:FINAL_SUMMARY:END -->
