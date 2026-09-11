---
id: TASK-51
title: 'Attribution gap: large boards with 0/N matched despite real kept postings'
status: To Do
assignee: []
created_date: '2026-09-11 10:49'
updated_date: '2026-09-11 13:05'
labels: []
dependencies: []
ordinal: 51000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The job-attribution priority fix landed 2026-09-11 (content-first fuzzy match, then board fallback -- pflege_jobs/registry.py Matcher.match/_match_content/_match_jd/_match_board). Two boards in the 220-board delivery pass kept a large batch of real nursing postings but matched ZERO of them to any clinic_id: ukw.de (dvinci, Universitätsklinikum Würzburg, 42 kept / 0 matched) and jobs.ebel-kliniken.com (talention, 9 kept / 0 matched, 7 distinct employer names on one board). karriere.ameos.eu is also inconsistent across its own two board-url variants: one variant matched 204/204 in an earlier run, the bare-domain variant returned raw=0 in this run. A 0/N match on a single-employer or well-known-group board suggests either the employer_name string on these postings doesn't fuzzy-match the registry's operator/name fields at all (encoding issue, legal-entity-suffix mismatch, or a name the registry doesn't have), or the board_clinic_ids passed to Matcher.match() for these boards is empty/wrong so board fallback never has a pool to try.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 ukw.de's 42 unmatched postings: root cause found (employer_name string vs registry mismatch, or missing board membership) and fixed
- [ ] #2 jobs.ebel-kliniken.com's 9 unmatched postings across 7 employers: each of the 7 employer names checked against the registry, gaps fixed (new clinic row, alias, or operator string fix)
- [ ] #3 karriere.ameos.eu's two board-url variants reconciled -- confirm both point at genuinely different content or merge them to one board entry
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11 follow-up: karriere.ameos.eu (TASK-48) delivered 772 raw / 204 kept nursing postings after a real code fix (nested <span> inside job-link anchors was silently dropping every link -- see crawlers/vendor_adapters.py _job_link_pairs). All 204 matched to clinic_id 18501 (Neuburg) and ZERO to 27706 (Inntal, newly merged into the same board) -- exactly the same 'suspiciously all-to-one-clinic' pattern already flagged for AMEOS/kbo boards. Root cause is almost certainly the same org-name-defaulting: crawl_wp_jobs's parse_job_page fallback sets employer_name = c['name'] where c is the board's clinic0, so every row's employer_name trivially R1_exact-matches clinic0 regardless of which site actually posted it. Same phenomenon reproduced independently on meinkrankenhaus2030.de (Weilheim+Schongau shared board, all 17 rows R1_exact-matched to whichever clinic was clinic0).
<!-- SECTION:NOTES:END -->
