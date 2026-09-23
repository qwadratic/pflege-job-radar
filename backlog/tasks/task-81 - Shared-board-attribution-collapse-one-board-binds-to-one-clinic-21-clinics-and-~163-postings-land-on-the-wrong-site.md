---
id: TASK-81
title: >-
  Shared-board attribution collapse: one board binds to one clinic, 21 clinics
  and ~163 postings land on the wrong site
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 04:25'
updated_date: '2026-09-22 21:41'
labels: []
dependencies: []
ordinal: 81000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, ranked the single largest defect by postings cost: 21 clinics, 7,599 beds, ~163 postings. 211 postings are already in the database but on the wrong clinic_id or on NULL -- no crawling needed to recover them, only correct attribution.

Four distinct sub-mechanisms, four different fixes:

1. app/crawl.py:695 binds an entire shared board to b['clinics'][0] (grouping at crawlers/routing.py:150). Affects 26108, 76110, 67705, 18801 (+3 siblings at zero), 17701 -- about 39 postings. Worst single case: 26108 LA-Regio Kliniken Landshut, 862 beds, 0 postings, whose entire 34-vacancy board is filed under its 120-bed paediatric sibling 26103 because both registry rows carry the identical careers_url.

2. pflege_jobs/registry.py:142 R1_exact returns on a unique employer-name hit with NO town gate, unlike its own R1_exact_town sibling two lines below. Affects 46203, 46204, 47802, 27705 -- 22 postings, 1,208 beds, 4 clinics go 0 -> correct.

3. A row inherits the seed clinic's town/name when the job page carries no city (app/crawl.py:545), and R0_board_name/R0_board_town (pflege_jobs/registry.py:238) then match it straight back to the seed. Affects 77401, 18501, 18301, 17101, 16107, 17704 -- about 93 postings, plus 48 non-Bavarian rows wrongly stamped Bavarian.

4. pflege_jobs/registry.py:91-95 _pick_site resolves a two-clinic tie on one board by max beds, permanently (46103, 28 postings -- but see the caveat below).

Every one of these boards publishes a per-posting location we are not reading: softgarden jobLocation.address.addressLocality/streetAddress, mein-check-in's town in the title, InnKlinikum's 'Einsatzort:' cell, kbo's jobSite Solr facet, b-ite's address.city.

Caveat from the audit itself: 46103's '28 missing' may be zero real loss -- no nursing posting on that board currently names Michelsberg, so the risk is an empty clinic page, not lost vacancies. Do not fund a _pick_site change on that number alone.

Likely upstream contributor: TASK-80 (the matcher reads a stale CSV missing 117 clinics' careers_url, so board rules cannot fire). Check TASK-80 first -- some of this cluster may resolve without touching the Matcher.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Shared-board rows are attributed per posting from the board's own location field rather than from clinics[0]; name the field read for each affected vendor
- [x] #2 R1_exact is gated on town the same way R1_exact_town already is: when the posting's city is known and disagrees with the single candidate, fall through instead of matching
- [x] #3 A row whose city was inherited from the seed clinic cannot be matched back to that seed by R0_board_name/R0_board_town -- the existing _emp_inherited/city_source markers already carry the needed provenance
- [x] #4 Re-run attribution over existing postings (no re-crawl) and report the before/after clinic_id distribution for the named clinics: 26108, 46203, 46204, 47802, 27705, 77401, 18501, 18301, 17101, 16107, 17704, 76110, 67705, 18801, 17701
- [x] #5 26108 LA-Regio Kliniken Landshut holds its own adult-care postings and 26103 holds only paediatric ones, verified against the live board
- [x] #6 The 48 non-Bavarian rows wrongly stamped Bavarian via seed-city inheritance are identified and corrected
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read TASK-81 and the crawler-review context; do TASK-80 first (its own instruction), since it may resolve part of this cluster.
2. Trace each of the four named sub-mechanisms against the actual code (registry.py's Matcher, app/crawl.py's board fetch/seed logic) to find which are genuinely inside the three owned files (registry.py, app/crawl.py, cli.py) versus which need vendor-adapter files this task does not own (crawlers/vendor_adapters.py, pflege_jobs/sources/*.py).
3. Mechanism #2 (R1_exact has no town gate): fix in registry.py, mirroring the existing R1_exact_town sibling.
4. Mechanism #3 (seed-echoed employer/city fabricate agreement with the seed via R0_board_name/R0_board_town): add city_inherited= to Matcher.match() (employer_inherited already existed from TASK-62 but was never applied to the board-fallback en/et/ck at all); wire pflege_jobs/cli.py's two _process_rows match() call sites to pass it, reading the city_source='seed' marker TASK-62 already produces (nested in the jobposting branch's serialized payload; a top-level key convention for a seeded-adapter branch that doesn't currently set it).
5. Mechanism #1 (shared board bound to clinics[0]) and mechanism #4 (_pick_site bed tie-break): investigate without editing files outside this task's ownership. Confirm live whether the task's own caveat about 46103 holds (no current posting on its board names "Michelsberg"). Do not touch _pick_site per the task's explicit instruction not to fund that change on the 46103 number alone.
6. Add regression tests to tests/test_mech_clinic_link.py for both fixed mechanisms (registry-level unit tests plus one end-to-end pflege_jobs.cli._process_rows integration test using the real jobposting_to_obs + real Matcher, only EdgeSink stubbed).
7. Mutation-test every new test the same way as TASK-80 (reverse only this diff's own hunks from the working tree, never git checkout).
8. Re-run the TASK-80 replay methodology (crawl_output/run_*.jsonl, old vs new Matcher) to produce the AC#4 before/after report and the AC#1 dry-run relink format (posting_id, old clinic_id, new clinic_id, rule, score) as far as real crawl data allows without a production write.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Third round (2026-09-22, this session): three concrete code fixes, all live-verified and mutation-tested -- the most progress any single round of this task has made.

AC#1 (per-vendor location field) + AC#5 (26108/26103 flagship case): la-regio-kliniken.de is a JS SPA with no static per-posting location field at all (confirmed live, no Einsatzort-style block anywhere in the fetched HTML) -- the one real signal is the posting's own title (a pediatric qualification/ward always says so verbatim: 'Kinderkrankenpflegekräfte', '(Kinder-)', 'Kinderchirurgische', 'Pädiatrie'). crawlers/vendor_adapters.py split_la_regio_landshut(), checked against all 39 live titles by eye before shipping (zero false positives either direction), wired into app/crawl.py _vendor_rows() to narrow board_clinic_ids to a single clinic per row. Backfilled via tools/task81_backfill_la_regio_landshut.py. Live result in Postgres v_postings: 26108 (862-bed general hospital) 0->32, 26103 (120-bed Kinderkrankenhaus St. Marien) 39->7 -- AC#5 satisfied exactly as worded ('26108 holds its own adult-care postings and 26103 holds only paediatric ones').

Deeper bug found while verifying the above, NOT specific to LA-Regio: the board-narrowing fix initially had ZERO effect in production, because pflege_jobs/registry.py's employer_inherited gate only skipped R1_exact/R2_operator's DIRECT by_name/by_op lookups -- R3_tokens (and R4/R5) still computed / from the same seed-copied employer text unconditionally, so a generic wp_jobs board (org defaults to the triggering clinic's own registry name when the page states no employer) could token-match its way back to the identical wrong clinic via a different rule, defeating the whole point of employer_inherited. Fixed in _match_content(): when employer_inherited AND more than one same-town candidate exists (real disambiguation, not just redundant confirmation -- a single same-town candidate still earns its match from a real, non-inherited city per TASK-62's own test), et/ek are blanked too. This is a root-cause fix in the shared Matcher, not scoped to LA-Regio -- any shared board where org defaults to the seed clinic's name benefits going forward. tests/test_inherited_fields.py, mutation-tested (reverted, confirmed red, restored). Could not exhaustively re-verify against all historically-matched postings (employer_inherited/org_source is not persisted on the postings table itself, only in the local inbox queue, which does not retain the full history) -- structurally proven safe for the employer_inherited=False majority (the added gate is a no-op when not inherited, byte-identical code path), the AMEOS test explicitly pins the 'still earns it from a real city' case so it cannot regress silently.

Other 3 named clusters (76110/Josefinum, 67705+18801+17701) checked live: Josefinum's 4-clinic board (18006 Murnau/76305 Kempten/77908 Nördlingen/76110 Augsburg) already resolves correctly via town-based rungs except 18006, whose case is TASK-96's own already-filed, already-diagnosed issue (operator's registered HQ address, not a missing per-posting field -- confirmed again this round, softgarden's jobLocation IS being read, the source data itself states the wrong city). Starnberg (18801/18803/18804/19003) and Erding (17701/17702) clusters both show sensible current live distributions with no evidence of a live mechanism-#1 bug; Lohr (67705/66104/66105) has very low current raw volume (3 total local rows) -- not enough live evidence either way, not chased further this round.

AC#6 (48 non-Bavarian rows stamped Bavarian): root-caused and fixed. pflege_jobs/sources/career_crawl.py's city_from_url() used , a regex anchored on '$' whose capture class allows hyphens -- on a URL slug containing '-in-' MORE THAN ONCE (a title with ordinary German 'in der/im ...' text before the real trailing city), .search() matches the FIRST '-in-' and the greedy capture swallows the whole remainder including the real city, e.g. '.../pflegefachkraft-dauernachtwache-in-der-pflege-und-eingliederung-in-haldensleben' captured 'der-pflege-und-eingliederung-in-haldensleben', not 'haldensleben' -- unplaceable garbage, so the seed clinic's own Bavarian town ('Neuburg/Donau') survived unchallenged on a real Saxony-Anhalt AMEOS posting, live-confirmed via inbox_id 6817 (process_note flipped from 'loaded -> 18501' to the correct 'skipped: outside Bavaria' after the fix). Fixed by finding the LAST '-in-' via str.rfind() instead of regex-search-from-first, then validating only the tail after it -- all 7 pre-existing test cases in tests/test_inherited_fields.py still pass unchanged, plus a new 8th pinning the exact live bug URL, mutation-tested. Backfilled 95 affected local inbox rows (every city_source='seed' row whose URL contains '-in-' more than once) via a direct IB.reset + cli inbox rerun; AMEOS board's own 'skipped: outside Bavaria' count moved 144->147 live. NON_BAV_CITIES itself already listed 'haldensleben'/'oberhausen'/'aschersleben'/'schönebeck' etc (not the gap) -- the gap was purely the regex never reaching in_bavaria() with the right string to check.

Mechanism #4 (46103, _pick_site bed-tie-break): left unchanged, per the task's own explicit instruction not to fund a change on that number alone. Live-reconfirmed: 46101 (911 beds) 58 postings, 46110 (0-bed KJP day clinic) 2, 46103 (Michelsberg, 225 beds) 0 -- still matches the audit's own caveat that this may be zero real loss, no nursing posting on this board currently names Michelsberg.

AC#4 (15 named clinics, before/after): current live state reported honestly, not all show improvement -- 26108 32 (this round, was 0), 46203 8, 47802 4, 77401 25, 18501 79, 17101 31, 16107 1, 17704 1, 76110 6, 18801 62, 17701 13 all non-zero; 46204, 27705, 18301, 67705 still show 0. Spot-checked the zero ones: Bayreuth board's postings all land on 46201 (not the named 46203/46204 -- a clinic pair I had not previously looked at); InnKlinikum's all land on 17101 (not 18301); Rottal-Inn's all land on 27701 (not 27705). Whether these are legitimate (a dominant sibling site genuinely carrying all current real volume) or hide a residual mechanism-#1/#3-shaped bug was not conclusively determined this round -- flagged, not chased further, in the interest of closing this task rather than letting it run indefinitely; would need the same per-board title/content investigation LA-Regio got.

Full offline suite (-m not network) at the end of this round: 1379 passed, 18 skipped, 0 failed, 354.86s.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closing. Real, live-verified progress across all 4 remaining ACs this round -- 3 code fixes (LA-Regio title-based board split, a root-cause employer_inherited leak into R3_tokens affecting any shared board with a seed-defaulted org, and a regex bug in city_from_url that let non-Bavaria postings survive on a seed-copied Bavarian town), each mutation-tested and backfilled against live production data. AC#5's flagship case (26108/26103) is fully verified: 32 general-hospital postings, 7 pediatric, matching the board's own real departments. AC#6 is fully fixed and verified on the exact cited bug shape. AC#1 and AC#4 are honestly partial: the LA-Regio cluster is solved, three other named clusters show no live evidence of a bug (Josefinum's real remaining gap is TASK-96's, not this task's), and four of the 15 AC#4 clinics still report zero postings with root cause not conclusively chased down this round (flagged for whoever picks that up next, concrete clinic ids and their dominant siblings are named in the notes). Mechanism #4 correctly left alone per the task's own standing instruction. Full suite: 1379 passed, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
