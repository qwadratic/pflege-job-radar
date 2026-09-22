---
id: TASK-84
title: >-
  Non-vacancy pages stored as open nursing postings: ~120 junk rows from loose
  JOB_PATH, loose link emission and a catch-all role class
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-22 02:59'
labels: []
dependencies: []
ordinal: 84000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21: about 16 clinics, 120 rows that are not vacancies at all -- news articles, category index pages, marketing landing pages, a PDF asset and a medical-glossary entry. Three compounding causes:

1. crawlers/vendor_adapters.py:466 JOB_PATH matches any URL containing /jobs/, so 117 of the 175 rows the München Klinik board returns are content pages. Its /(karriere-)?detail/[^/?#] alternative matches any /detail/ path, which is how klinikum-msp.de's glossary entry /patienten-besucher/glossar/detail/fusspflege is stored as an open nursing posting.

2. pflege_jobs/sources/career_crawl.py:334-335 emits a link as a posting whenever JOB_HREF matches, the anchor text is longer than 6 characters and LIST_NAV.fullmatch() does not fire. LIST_NAV lists the bare word 'pflege', so 'Pflegedienst' and 'Ansprechpartner' both pass. This costs in BOTH directions: 12 junk rows at 56101, and at 36202 the category link is emitted as a posting and therefore never enqueued as a list page, so the BFS never reaches /alle-stellenangebote and 9 of 14 real vacancies are never seen.

3. classify_role falls back to ('sonstige_pflege','fallback') for any title containing a Pflege token, and sonstige_pflege is NOT in patterns.json:344 excluded_role_classes. Verified: classify_role('PFLEGEN KÖNNEN.') returns ('sonstige_pflege','fallback'). That is how 31 Memmingen news headlines became open postings.

Net effect measured at Memmingen: shows 40 postings, holds 9 real ones.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A link is only emitted as a posting when it carries a posting-shaped signal (gender marker in the anchor, or JSON-LD JobPosting on the fetched page) -- and a link that fails that test is still enqueued as a list page rather than dropped, so 36202 reaches /alle-stellenangebote and recovers its 9 missing vacancies
- [x] #2 JOB_PATH's /detail/ alternative requires a job-ish parent segment, and NOT_JOB_PATH excludes glossary/news/press/event paths; klinikum-msp.de's glossar/detail/fusspflege no longer qualifies
- [x] #3 sonstige_pflege's fallback behaviour is decided explicitly: either it stops being a catch-all for any Pflege token, or it joins excluded_role_classes -- state which and why, and pin it with the 'PFLEGEN KÖNNEN.' case
- [ ] #4 The ~120 existing junk rows are retro-purged; report the count removed per clinic and the new open_jobs total
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Cause #1 (vendor_adapters.JOB_PATH matching any /detail/): tightening JOB_PATH itself conflicts with an existing pinned test (test_not_job_path_excludes_typo3_news_press_blog_event_detail_pages, which requires JOB_PATH to still match bare /aktuelles|presse|blog/detail/ so NOT_JOB_PATH has something to positively exclude) -- extended NOT_JOB_PATH's blacklist with "glossar" instead, fixing the confirmed klinikum-msp.de case without narrowing JOB_PATH's own contract.
2. Cause #2 (career_crawl.py emitting any JOB_HREF-matching, non-list-nav-fullmatch link as a posting): a link is now only emitted as job_links on its OWN anchor text carrying a gender marker (JOB_TEXT); anything else that looked job-shaped (JOB_HREF) or list-shaped is queued as a list page instead of dropped, AND every fetched list page is now also checked for embedded JobPosting JSON-LD. Verified against a reconstruction of the 36202/St. Josef Regensburg case (category link -> real listing -> real jobs, all 3 hops now reached).
3. Cause #3 (classify_role's unconditional sonstige_pflege fallback): now requires the title itself to carry a posting-shaped signal (gender marker), else falls to nicht_pflege/fallback_no_posting_signal. Decision per AC#3: narrowed the fallback rather than adding sonstige_pflege to excluded_role_classes wholesale, because that would ALSO drop the real, already-pinned "Betreuungskräfte (m/w/d) gesucht" and section-confirmed "Gerontofachkraft (w/m/d)" cases (tests/test_mech_role_class.py, tests/test_classify_section.py) -- both still pass unchanged.
4. Tests added/mutation-tested via /tmp copies for all three causes, using real confirmed-live examples (München Klinik clinic 16201's "PFLEGEN KÖNNEN." and siblings; ANregiomed 56101's 15/15-junk vanity-domain rows; klinikum-msp.de glossary path).
5. AC#4 (retro-purge): production write access refused this round -- produced a DRY-RUN report (backups/task84_purge_dryrun_20260922T024753Z.txt + _ids_.json). Deliberately narrow method (see script docstring): isolates ONLY rows where (a) the pre-fix fallback would have produced sonstige_pflege, (b) the landed fix now excludes that same title, AND (c) the row's CURRENTLY STORED role_class is itself sonstige_pflege -- this avoids two false-positive classes a naive old-vs-new diff produced: rows that legitimately needed nursing_section_confirmed=True (not replayable from stored data, e.g. real Gerontofachkraft rows) and unrelated stale-classification drift from an earlier, never-backfilled patterns.json fix (e.g. an ausbildung/ota_ata reordering). Result: 81 rows across 34 clinics, -81 open_jobs overall. This is narrower than the ~120/16-clinic original audit ON PURPOSE: causes #1/#2 only change what gets crawled going forward, not a title already stored, so their share of the ~120 only shows up after the next crawl.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Full offline suite (once, at end): 1336 passed, 1 skipped, 0 failed, 1197 deselected (-m "not network"), 342s.

AC#1 nuance: mechanism (gender-marker-or-JSON-LD requirement; non-matching JOB_HREF links queued as list pages instead of dropped; every fetched list page also JSON-LD-checked) is implemented and mutation-tested (3 tests in tests/test_career_crawl_section.py, each independently confirmed red against the reverted code via a /tmp copy). Live re-crawl of clinic 36202 (csj.de) today: 32 rows either way, PRE-FIX and POST-FIX code give IDENTICAL results for this specific board right now -- likely the board's own structure changed since the 2026-09-21 audit, or the pre-existing section-first path already covers it independent of this fix. Could not isolate a live causal "9 recovered" delta for this exact board today; the mechanism itself is proven by the unit tests, which reconstruct the described failure shape (category link -> real listing -> real jobs) directly.

AC#2 nuance: did NOT narrow JOB_PATH's own /detail/ alternative as literally described -- that conflicts with an existing pinned test (test_not_job_path_excludes_typo3_news_press_blog_event_detail_pages, tests/test_vendor_adapters.py) which requires JOB_PATH to still match the bare /aktuelles|presse|blog/detail/ shape so NOT_JOB_PATH has something to positively exclude. Added "glossar" to NOT_JOB_PATH's blacklist instead; the concrete, testable outcome the AC names (klinikum-msp.de's glossar/detail/fusspflege no longer qualifies) is proven (new pinned test, mutation-tested).

AC#4 evidence: backups/task84_purge_dryrun_20260922T024753Z.txt (+ _ids_.json). Deliberately narrow method (see script docstring in backups/task84_purge_dryrun_script_20260922T024753Z.py): only counts a row when (a) the PRE-fix fallback would reach sonstige_pflege for that title, (b) the LANDED fix now excludes it, and (c) the row's currently stored role_class is itself sonstige_pflege. This avoids two false-positive classes a naive stored-vs-recomputed diff produced on the first pass: rows that legitimately needed nursing_section_confirmed=True (not replayable from stored data -- e.g. real "Gerontofachkraft (w/m/d)" rows) and unrelated stale-classification drift from an earlier, never-backfilled patterns.json change (e.g. an ausbildung/ota_ata pattern-order fix). Result: 81 rows across 34 clinics, -81 open_jobs. Confirmed real, concrete finds along the way: clinic 56101 (ANregiomed Ansbach) currently has 15/15 open rows on its vanity domain that are ALL non-vacancy pages (news/FAQ/contact/application-process pages) -- matches the audit's "12 junk rows at 56101" closely; clinic 16201 (München Klinik Schwabing) literally contains "PFLEGEN KÖNNEN." (posting_id 6795/10289) and "Intensivpflege MACHEN KÖNNEN." (6794), the exact classify_role('PFLEGEN KÖNNEN.') example the task cites; clinic 67702 posting 12153 "Fusspflege - Klinikum Main-Spessart" is the klinikum-msp.de glossary row named in AC#2 (also independently caught by the NOT_JOB_PATH fix at crawl time). This is narrower than the ~120/16-clinic original audit ON PURPOSE -- causes #1/#2 change what gets CRAWLED going forward, not a title already stored, so their share only shows up on the next crawl; re-running this same script after that crawl is how to see the fuller number.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Three fixes, one per cause, none re-introducing a crawler-side classify filter (TASK-95): (1) NOT_JOB_PATH (vendor_adapters.py) now also excludes glossary paths -- klinikum-msp.de's glossar/detail/fusspflege no longer qualifies; JOB_PATH itself deliberately left unnarrowed, a pinned test requires it to still match the bare news/press/blog shape. (2) career_crawl.py's _crawl_urls now only emits a link as a posting on its own gender-marked anchor text; anything else that looked job-shaped is queued as a list page instead of dropped, and every fetched list page is now also checked for embedded JobPosting JSON-LD. (3) classify_role's sonstige_pflege fallback now requires the title itself to carry a posting-shaped signal (gender marker) -- classify_role('PFLEGEN KOENNEN.') now returns nicht_pflege/fallback_no_posting_signal instead of a kept posting; the already-pinned real cases ("Betreuungskräfte (m/w/d) gesucht", section-confirmed "Gerontofachkraft (w/m/d)") are unchanged.

AC#1, AC#2, AC#3 checked -- see implementation notes for two honest nuances: AC#1's mechanism is mutation-tested but a live re-crawl of clinic 36202 today shows no pre/post differential (board content likely changed since the 2026-09-21 audit); AC#2's concrete outcome is proven via NOT_JOB_PATH rather than the literally-described JOB_PATH narrowing (which conflicts with an existing pinned test). AC#4 left UNCHECKED: per this round's explicit "do NOT mutate production data" instruction, produced a DRY-RUN report instead of purging (backups/task84_purge_dryrun_20260922T024753Z.txt) -- 81 rows / -81 open_jobs found across 34 clinics, using a deliberately narrow method that isolates only this fix's own effect (see notes) rather than every stale classification. This is smaller than the ~120-row audit on purpose: causes #1/#2 only change what gets crawled going forward, not a title already stored. Task is not genuinely done until either that purge is applied or dry-run-only is accepted for this round -- left In Progress rather than Done.

Full offline suite: 1336 passed, 1 skipped, 0 failed (-m "not network"), baseline was 1310/1/0.
<!-- SECTION:FINAL_SUMMARY:END -->
