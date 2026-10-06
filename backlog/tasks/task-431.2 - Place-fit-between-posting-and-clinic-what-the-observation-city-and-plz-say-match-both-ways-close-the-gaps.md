---
id: TASK-431.2
title: >-
  Place fit between posting and clinic: what the observation city and plz say,
  match both ways, close the gaps
status: In Progress
assignee: []
created_date: '2026-10-06 07:20'
updated_date: '2026-10-06 10:24'
labels:
  - registry
  - data-quality
  - geo
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 303000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Posting observations carry city and plz (5970 rows, 755 distinct pairs, 572 cities); the registry has 651 clinics in 287 towns and no plz. 354 of the 570 posting cities match no clinic town by plain string, 71 clinic towns have no posting city. Classify the 354: spelling variant, district or ward of a clinic town, branch of an operator in another town, place outside Bavaria, wrong value from the parser. Then make the place link work in both directions: from a posting city or plz to the clinic or clinics there (and to the operator), and from a clinic to every place its postings name. Fix what is wrong at the source (parser or adapter, not by hand per row). Fill the clinic plz if a reliable source exists. Uses pflege_jobs/geo.py and data/geo/gemeinden_de.csv; the mirror snapshot tests/fixtures/mirror_infra/infra__registry-read-proxy.sqlite.xz holds the pairs and can be reduced to the 755 distinct pairs afterwards.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The 354 unmatched posting cities are classified with counts per class
- [ ] #2 A documented function or view maps a posting place to clinics and a clinic to its posting places, tested on the mirror pairs
- [ ] #3 Fixes for wrong values land at the source with red tests first; DB writes only after Ivan approves the exact counts
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Research done 2026-10-06 (read-only; scripts and logs in the job tmp dir 431/place: classified354.txt/.json, placelib.py prototype, logs/*.txt, run_all.sh).
Base: run 233 = 16285 jobposting + 5415 observation lines; production kept 3343 (2933 linked, 410 unlinked). Snapshot 5970 rows / 755 pairs / 572 cities and 354 unmatched cities / 71 clinic towns reproduced.
How the place is used: the city is never a link source. It is a veto on R1/R2 (refuses only if the city names ANOTHER registry town), the sole locator for R3/R4/R5 and board rules, and a tie-break among sites; city_key already folds 'a.d.', 'i.d.', '(...)', ', ...'. Clinic PLZ unused (0/651).
354 cities (cities / postings): spelling variant 66/243; district 7/30; operator branch in a place without clinic 18/43; other real Bavarian place without clinic (care homes via stepstone/allgaeuer) 122/204; outside Bavaria 100/204; non-place 22/79 (organisation name 56, bundesweit/Deutschland 22, street 1); parser garbage 4/6; several places in one string 5/5; bare ambiguous name (Neustadt, Taufkirchen) 7/14; unresolved 3/3. 49 percent of postings (weighted) are in places with no clinic by definition or outside Bavaria. Spelling variants and districts are NOT a link gap: city_key resolves 70 of 73 (271 of 273 postings).
Damage (5136 postings): 830 (16 percent; open 751 of 3563 = 21 percent) sit in a city that is no clinic town; 224 of them linked, 606 not. Of 3834 linked: same municipality 3687, other municipality 117 (3.1 percent, 77 open), place unresolved 30. The 117: distance under 10 km 4, 10-30 km 59, 30-100 km 24, over 100 km 30 (e.g. Dachau to Erlenbach 238 km, expired; Noerdlingen to Donauwoerth 12 open). Cause: links are only set, never revoked: 152 of 1983 re-seen stored links differ from the current code, 144 would now be unlinked. Current code replayed on run 233 reproduces production (2908/2908) and links only 4 of 3298 loaded rows to another municipality. Unlinked because of the place: about 0 (278 of 390 loaded-unlinked sit in towns with 2+ clinics: place right, site undecidable).
Proposal: place_of(city, plz) -> (ags, rule) in geo.py from gemeinden_de.csv + GeoNames DE.txt (CC BY, external; the csv has one PLZ per municipality, so it cannot map PLZ to place alone); clinics.ags + clinics.plz; postings.place_ags / place_rule; views place -> clinics (+operator), clinic -> places, link-vs-place check. Prototype resolves 4816 of 5136 postings (93.8 percent). Simulated gain in link counts about 0 (run 233: +1 new link, a false one). Real value: the check view flags the 117 and 30 unresolved; 47 clinics name 2+ places (Diakoneo Rangauklinik Ansbach 11). Place is coarse: 517 of 651 clinics share a municipality with another clinic.
Clinic PLZ: best source already in the repo, data/registry/krankenhausverzeichnis_24.xlsx (Statistische Aemter, 31.12.2024, street + PLZ per site): 626 of 651 (96.2 percent): RH 229/229 exact id, DK 13/13 from the source text, KeZ 384/409 by domain/name match; 25 left (Muenchen x10, multi-site towns, 2 without row). Checks: klinikradar equal 324/325, modal posting PLZ equal 213/224. gemeinden_de PLZ differs from the official one for 147 of 626. PLZ pays off: of 312 stored R6 site ties PLZ picks one in 55, 35 differ from the stored pick (Klinikum Nuernberg Nord/Sued 19, Muenchen Klinik Harlaching 13).
Place fields in observations (6119): city 99.9 percent, valid PLZ 39.9 percent, region 43 percent, lat/lon 10 percent, n_locations always 1. Clean: softgarden, uk-erlangen, ukw, tum, pi-asp, kbo.de, mein-check-in, diakoneo. Seed town as city (no signal): krankenpflegejobs24 80 percent, lmu wp_jobs 93 percent, ameos 73 percent. Bad: stepstone 1017 postings, PLZ 0 percent, 219 bad places, 203 open non-Bavarian postings in the table; barmherzige group (organisation name in city, 29 percent); jobs.sana.de 'Deutschland'; regiomed bare 'Neustadt'; asklepios comma lists; fixture row 12843 (x.example) open.
Side finding: geo.clinic_centroid ignores landkreis: 6 clinics get the wrong municipality (DK01 Bruckberg, 57403 Altdorf, 18302 Haag, 18710 Aschau, RH2421 Auerbach, RH2229 Bernried).
Unsolvable here: bare names without PLZ, non-place values (adapter fix), multi-place strings, 1132 postings (22 percent) whose city is the seed clinic town, site choice in multi-clinic towns, non-Bavarian places. Not verified: no oracle for the true clinic (117/144 not hand-labelled), C1/C2 split is a token heuristic, KeZ-to-KHV match is heuristic, ward rule tested only on Muenchen/Erlangen.
Status: awaiting Ivan's decisions on the change list; no data changed.

2026-10-06 PLZ WRITTEN (pflege-clawl, Ivan: write the reliable ones). PR #14 merged (bad6466); pflege-ingest edge function v15 deployed through the Supabase connector (plz in the clinic columns; a clinic row sent without plz keeps the stored value; smoke test without writes: wrong secret 403, empty body ok); sql/015 applied (reason code source_supplement). tools/fill_clinic_plz.py got --rules (4179979, red test first) and was run with rhv_id, dk_source, khv_domain, khv_only_site_in_municipality --apply --by pflege-clawl: 532 PLZ set, backup backups/fill_clinic_plz_20261006T102258Z.json, read back: 532 of 651 clinics have a PLZ, 532 correction rows with reason source_supplement. Held back: 94 (khv_name_overlap 72, khv_municipality_one_plz 22) and 25 unresolved: data/registry/plz_review.csv, TASK-431.7. Effect on the public API shows after the next restart of pflege-web (geo_source plz for the 532).
<!-- SECTION:NOTES:END -->
