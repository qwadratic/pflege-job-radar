---
id: TASK-51
title: 'Attribution gap: large boards with 0/N matched despite real kept postings'
status: Done
assignee: []
created_date: '2026-09-11 10:49'
updated_date: '2026-09-11 14:41'
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
- [x] #1 ukw.de's 42 unmatched postings: root cause found (employer_name string vs registry mismatch, or missing board membership) and fixed
- [x] #2 jobs.ebel-kliniken.com's 9 unmatched postings across 7 employers: each of the 7 employer names checked against the registry, gaps fixed (new clinic row, alias, or operator string fix)
- [x] #3 karriere.ameos.eu's two board-url variants reconciled -- confirm both point at genuinely different content or merge them to one board entry
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-11 follow-up: karriere.ameos.eu (TASK-48) delivered 772 raw / 204 kept nursing postings after a real code fix (nested <span> inside job-link anchors was silently dropping every link -- see crawlers/vendor_adapters.py _job_link_pairs). All 204 matched to clinic_id 18501 (Neuburg) and ZERO to 27706 (Inntal, newly merged into the same board) -- exactly the same 'suspiciously all-to-one-clinic' pattern already flagged for AMEOS/kbo boards. Root cause is almost certainly the same org-name-defaulting: crawl_wp_jobs's parse_job_page fallback sets employer_name = c['name'] where c is the board's clinic0, so every row's employer_name trivially R1_exact-matches clinic0 regardless of which site actually posted it. Same phenomenon reproduced independently on meinkrankenhaus2030.de (Weilheim+Schongau shared board, all 17 rows R1_exact-matched to whichever clinic was clinic0).

2026-09-11 resolved, all 3 ACs:

#1 ukw.de: Matcher.match() already worked correctly on direct test (R3_tokens, 'Universitätsklinikum Würzburg' -> 66390) -- the 0/42 in the original run was stale, from before today's earlier priority-fix rewrite landed mid-flight in the 220-board pass. Re-delivered live: 42/42 matched, 0 code change needed.

#2 jobs.ebel-kliniken.com: investigation found a REAL Matcher bug, not a missing-registry-data gap as originally guessed. pflege_jobs/registry.py's R3/R4 token-overlap loop had a fallback (`if not ok and ek & x["_kinds"] and len(et) <= 1: ok = True`) meant to handle an employer name that reduces to nothing but a stopword after the kind-word ("Klinikum Fürth Personalabteilung"/"AöR") -- but it fired for ANY single leftover token, including a genuine distinguishing one. Reproduced concretely: 'Klinik Reinhardshöhe GmbH' (a real Ebel-group site in Bad Wildungen, Hesse -- not Bavaria) was false-matched to 'Klinik Bad Windsheim' (Bavaria, clinic 57502) purely because city_key() collapses every "Bad X" town to the single key "bad", and Bad Windsheim's own name/operator carry no distinguishing token to rule it out. Removed the fallback (existing tests + a new regression test in tests/test_mech_clinic_link.py both pass without it -- the documented use cases it was meant for already resolve via the primary `not et - ek` check). Re-delivered live: 0/9 matched, which is now the CORRECT answer -- all 9 kept postings are for other Ebel-group sites outside Bavaria; the board's 2 real Bavaria clinics (Klinik am Park / Klinik am Park Bad Steben) simply have no current openings.

#3 karriere.ameos.eu: reconciled 2026-09-11 (TASK-48) -- both board-url variants are the same portal; the bare-domain one had a wrong careers_url (portal homepage, not the listing) and is now merged into the working board. The separate 'org-name-defaults-to-clinic0' bias noted in this task's earlier followup (AMEOS 204/204-to-one-clinic, meinkrankenhaus2030 17/17-to-one-clinic) is real but distinct from the false-positive bug above and from the original 3 ACs -- worth its own follow-up task if it recurs, not chased further here since neither reproduction showed a WRONG answer (just an unverified one -- content-matching correctly found Neuburg/Weilheim-or-Schongau as A valid answer, just not provably the RIGHT one of several candidates sharing a board).

2026-09-11 re-link audit of already-written prod rows: pulled all 411 postings whose clinic_id was set via a fuzzy rule (R3_tokens/R4_tokens_op/R5_loose/R6_ambiguous_sites and their _full/_bestj variants) and re-ran the fixed Matcher.match() (content-only, no board context) against each. 359 unchanged. 52 differed -- split into two groups:

1. CONFIRMED bug, fixed live: posting_id 7543 and 10262 ("Krankenhaus fuer Psychiatrie... Schloss Werneck", city "Bad Brueckenau") were matched to clinic 67307 (Bad Neustadt a.d. Saale) purely via the city_key()-collapses-every-"Bad X"-town-to-"bad" + the now-removed len(et)<=1 fallback -- the employer name shares zero real tokens with either Bad Neustadt or the real Bad Brueckenau clinic (67205 HESCURO). Set clinic_id/clinic_match_rule/clinic_match_score to NULL (correctly unmatched -- neither candidate is right, and 67205 is a different employer entirely).

2. NOT touched, needs board-aware re-verification before acting: the other 50 (Artemed SE x9 @ Berg/Feldafing, "Kliniken des Bezirks Oberbayern" x7 @ Muenchen, Diakoneo KdoeR x19 @ Nuernberg, Rotkreuzklinikum Muenchen gGmbH x5, Klinikum Bamberg x5) all differ for reasons UNRELATED to the len(et)<=1 fix:
   - Artemed SE / kbo cases go content-match -> None because my re-link script called m.match(emp, city) WITHOUT the board= parameter (no stored board_clinic_ids per posting) -- these were very likely originally resolved via _match_board's R0_board_town fallback using the real shared-board clinic list, which a content-only re-check can't reproduce or fairly judge. Re-verifying these needs reconstructing each posting's board_clinic_ids (via routing.plan() + external_url) first.
   - Diakoneo (56404 vs 56406) and Rotkreuzklinikum (16215 vs 16223) cases are an R6_ambiguous_sites TIE-BREAK flip: _pick_site() now deterministically prefers the higher-bed site (56404=279 beds beats 56406=139; 16215=280 beats 16223=155), but the stored rows carry the LOWER-bed site -- looks like stale data from before _pick_site()/SITE_PREFERENCE existed, not something today's fix touched. Plausibly worth correcting (52 rows, minus the 2 already fixed = 50 raw list saved at /tmp/relink_changes.json this session, not committed anywhere durable) but deliberately left alone this session to avoid acting on an unverified diff.

2026-09-11 relink completed properly (board + description context, not the earlier content-only shortcut): reconstructed the real board_ids for each affected employer group (Artemed SE via jobs.smartrecruiters.com/ArtemedSE -> [18872,18105,18802,18808,76108]; kbo.de group portal -> all 32 clinics via crawlers.vendor_adapters.group_portal_for(); Diakoneo Nuernberg -> [56404,56406]; Rotkreuzklinikum Muenchen -> [16215,16223]; Sozialstiftung Bamberg -> [46101,46103,46170,47403]) and re-ran Matcher.match(employer, city, board=..., description=...) against all 52 previously-flagged postings.

Applied (29 rows, all confirmed safe):
- 22 Diakoneo KdoeR postings: 56406 (Cnopf'sche Kinderklinik, 139 beds) -> 56404 (Klinik Hallerwiese, 279 beds). Reproducible R6_ambiguous_sites tie-break under board context too -- confirms this is a stale pre-_pick_site() artifact, not board-dependent.
- 5 Rotkreuzklinikum Muenchen postings: 16223 -> 16215, same tie-break pattern.
- 2 Artemed SE postings (6368, 6370): 18808 (board-fallback guess) -> 16235 (Artemed Fachklinik Muenchen, R_jd_text -- the description uniquely names this site). Content match correctly outranks board fallback per Matcher's own priority order.
- 9 Artemed SE postings: confirmed unchanged (already correct via R0_board_town).

NOT applied, filed as TASK-57 (2 separate real gaps, neither a consequence of today's fix):
- 9 kbo.de postings: city defaults to 'Muenchen' even when the job's own URL names a different town (e.g. Garmisch-Partenkirchen) -- an upstream city-extraction bug in crawl_group_portal, not Matcher. With the wrong city, the 32-clinic board pool can't be narrowed and Matcher correctly declines to guess.
- 5 Bamberg postings: registry has two near-duplicate 'Klinikum Bamberg... Bruderwald' rows (46101 vs 46170) -- genuinely ambiguous until the registry is cleaned up.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Found and fixed a real Matcher false-positive (an overly permissive single-leftover-token fallback in registry.py's R3/R4 rules let a Hesse clinic match a Bavaria one purely via the city_key() 'Bad X' collapse-to-'bad' bug). Removed the fallback, added a regression test. ukw.de and ameos.eu's gaps were stale data from before today's earlier fix landed -- both re-delivered correctly with zero further code change. jobs.ebel-kliniken.com's correct answer is 0/9 matched (all non-Bavaria), not a gap.
<!-- SECTION:FINAL_SUMMARY:END -->
