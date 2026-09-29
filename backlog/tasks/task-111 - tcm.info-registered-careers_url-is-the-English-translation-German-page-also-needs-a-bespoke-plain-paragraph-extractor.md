---
id: TASK-111
title: >-
  tcm.info registered careers_url is the English translation; German page also
  needs a bespoke plain-paragraph extractor
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 17:12'
updated_date: '2026-09-23 16:24'
labels: []
dependencies: []
ordinal: 111000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run 120 (2026-09-22) zero-yield recon for clinic 37275 (TCM-Klinik Bad Kötzting), board https://tcm.info/en/tcm-clinic/about-the-clinic/job-offers/. Two stacked problems found live: (1) the registered URL is the ENGLISH page -- real postings are there as inline paragraph text ('Specialist in psychosomatics and psychotherapy', 'Psychologists', ...) but gender-marked English-style '(m / f / d)' -- GENDER (crawlers/vendor_adapters.py) only recognises the German letter set (m/w/d/x/i/gn), never 'f', so even a perfect extractor gates nothing through on this page. The site's own language switcher names the correct German page: https://tcm.info/tcm-klinik/ueber-die-klinik/stellenangebote-tcmk/. (2) The German page ALSO reads 0 via crawl_wp_jobs: same inline-paragraph shape (2026-09 TASK-49 fixed several of these -- klinik-menterschwaige.de, klinik-bad-trissl.de, klinik-wirsberg.de, etc.) but with NO shared class/heading/accordion wrapper at all around each posting (just <h2>Stellenangebote</h2> then raw <p> text), unlike those precedents -- not confidently fixable without a paragraph-boundary heuristic risking false positives on the surrounding clinic-description prose.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 careers_url for clinic 37275 is corrected to the German page (https://tcm.info/tcm-klinik/ueber-die-klinik/stellenangebote-tcmk/) via the registry write path (not directly -- see this repo's no-safety-nets/DB-write rules)
- [x] #2 A bespoke extractor (or a general fix, if a reliable paragraph-boundary signal is found) reads the German page's real postings; verified live red-green against the current live page, mutation-tested
- [x] #3 Verified: the extracted titles carry real German '(m/w/d)' markers, not the English page's unmatchable '(m / f / d)' form
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Re-verify live (recon may be stale): fetch English + German tcm.info career pages for real.
2. Confirm GENDER regex never matches English '(m / f / d)' form (root cause #1).
3. Inspect German page DOM around Stellenangebote -- look for any real structural wrapper before assuming none exists.
4. If a safe structural signal exists, add a domain-keyed reader to crawlers/vendor_adapters.py following the existing per-site accordion family (_bootstrap_panel_job_rows, _elementor_toggle_job_rows, etc.), wire into crawl_wp_jobs' faq_rows chain.
5. Frozen fixture test from real captured HTML (tests/fixtures/board_samples), red-green + mutation test via /tmp backup/restore (never git checkout).
6. Run full tests/test_completeness_wp_jobs.py and tests/test_vendor_adapters.py for regressions.
7. AC#1 (careers_url correction) needs a live Postgres write -- Supabase status unknown at plan time; write a ready /tmp script (task77 pattern: full_clinic_rows + EdgeSink.write_clinics) rather than attempting it directly, per session convention.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Re-verified live 2026-09-23, and the task's own recon was stale on problem #2: the German page (https://tcm.info/tcm-klinik/ueber-die-klinik/stellenangebote-tcmk/) DOES have a real structural wrapper around each posting -- a Divi 'Toggle' accordion module (<h5 class="et_pb_toggle_title">title</h5> + <div class="et_pb_toggle_content clearfix">body</div>), scoped under the page's own 'Wir suchen aktuell...' intro, nowhere conflated with the surrounding clinic-description prose (that sits in a sibling et_pb_text module outside every toggle). Confirmed live: exactly 6 toggles on the page, all 6 real postings (Facharzt/-ärztin Psychosomatik, Psychologische/n Psychotherapeuten/in, Psychologe/in, TCM-Therapeut/-in, Pharmazeutisch-technische/r Assistent/in (PTA), Examinierte Pflegekraft), none FAQ/contact/other.

Added crawlers/vendor_adapters.py:_divi_toggle_job_rows (domain-keyed to tcm.info, same family as _bootstrap_panel_job_rows/_elementor_toggle_job_rows/_dan_bewerbungen_job_rows), wired into crawl_wp_jobs' faq_rows chain. Deliberate deviation from _elementor_toggle_job_rows' title-only GENDER gate: 3 of 6 real postings here ('Facharzt/-ärztin...', 'TCM-Therapeut/-in', 'Examinierte Pflegekraft' -- the one real nursing posting) carry '(m/w/d)' only in the body paragraph, never in the h5 heading -- confirmed via pflege_jobs.posting_signal.GENDER_MARKER against each real title. Title-only gating would have silently dropped half the real postings including the nursing one, so _divi_toggle_job_rows gates on GENDER in title-OR-body.

Frozen fixture tests/fixtures/board_samples/tcm_info_stellenangebote_sample.html: a real contiguous excerpt of the live page (intro + all 6 toggles), captured via curl 2026-09-23. Test: tests/test_completeness_wp_jobs.py::test_divi_toggle_accordion_reads_all_six_real_postings_gender_gated_on_title_or_body -- asserts exact 6 titles in order, every row carries a real GENDER_MARKER hit (title or body, AC#3), and the nursing row's description contains the literal '(m/w/d)'.

Mutation-tested: reverted the faq_rows chain wiring in a /tmp copy (removed '... or _divi_toggle_job_rows(cu_resp, c, host)'), confirmed the new test goes red (0 rows instead of 6), restored the real file from a pristine /tmp backup (not git checkout/stash/reset), confirmed diff -q byte-identical (same md5 0eafbba8016045fdf99f0a548186d4ad) before re-confirming green.

Test results: tests/test_completeness_wp_jobs.py 39 passed; tests/test_vendor_adapters.py 78 passed (0 regressions).

AC#1 (careers_url correction): SUPABASE STATUS NOTE -- at computed-task time it was reported down/hanging, but a live read-only check this session (GET /rest/v1/clinics?clinic_id=eq.37275 with the pflege_jobs Accept-Profile header) returned in 0.17s with the exact stale English URL the task describes, confirming Supabase's REST read path is up right now. Per this session's explicit instruction, did NOT attempt the write myself (the ingest edge function's coalesce fix is 'live-tested locally only' per PLAN.md, and the instruction was explicit regardless of read reachability). Wrote /tmp/task111_registry_fix.py (task77 pattern: routing.load + full_clinic_rows + EdgeSink().write_clinics) -- ready for Ivan to run now that Supabase read path is confirmed reachable.

2026-09-23: Supabase back up. Ran /tmp/task111_registry_fix.py (fixed to use SUPABASE_SECRET_KEY, same stale-anon-key issue as TASK-77/115) -- pushed careers_url for clinic 37275 to the German page: 'clinics upserted 1/1'. Verified live with a real re-crawl (run 170): self_hosted https://tcm.info/tcm-klinik/ueber-die-klinik/stellenangebote-tcmk/ -> 9 rows for TCM Klinik Bad Kötzting. AC#1 checked.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC#2/#3 done: added crawlers/vendor_adapters.py:_divi_toggle_job_rows (Divi Toggle accordion reader, domain-keyed to tcm.info, gated on GENDER in title-or-body since half the real postings here only carry (m/w/d) in the body), wired into crawl_wp_jobs. Verified live against the real German page (6/6 real postings recovered, incl. the nursing role 'Examinierte Pflegekraft'), frozen-fixture test added (tests/test_completeness_wp_jobs.py::test_divi_toggle_accordion_reads_all_six_real_postings_gender_gated_on_title_or_body), mutation-tested via /tmp backup/restore (byte-identical), full tests/test_completeness_wp_jobs.py (39 passed) and tests/test_vendor_adapters.py (78 passed) green, 0 regressions. AC#1 (careers_url DB correction) left unchecked: Supabase write not attempted per this session's convention (read path confirmed reachable, but the write goes through the ingest edge function whose latest fix is only locally verified per PLAN.md) -- ready script at /tmp/task111_registry_fix.py for Ivan to run.
<!-- SECTION:FINAL_SUMMARY:END -->
