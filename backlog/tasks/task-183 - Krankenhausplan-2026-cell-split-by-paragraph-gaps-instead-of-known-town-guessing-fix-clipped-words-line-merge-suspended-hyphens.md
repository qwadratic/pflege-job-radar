---
id: TASK-183
title: >-
  Krankenhausplan 2026 cell split by paragraph gaps instead of known-town
  guessing; fix clipped words, '-' line merge, suspended hyphens
status: To Do
assignee: []
created_date: '2026-09-30 16:37'
labels:
  - registry
  - parser
dependencies: []
ordinal: 180000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-30 while deciding the 51 unexplained registry deviations (TASK-175 review, files /tmp/review51/). In every 2026 name cell the name, Standort and Traeger are separate paragraphs: lines inside a paragraph are ~9.7pt apart, paragraphs 17-21pt apart; all 401 rows split into exactly 3 this way (/tmp/review51/paras.py, paras_all_2026.json). Reading cells like that agrees with the DB everywhere except 39 of the 51 reviewed fields and 12 elsewhere (paras_all_diff.txt). Today's parser (_split_name_block) guesses the split with a known-towns list and has these bugs: (1) pdfplumber clips words at the column edge and the tails ('aft', 'ie', 'gen') land in the Status cell, where _status() strips them as footnote fragments although they belong to the name; (2) _split_name_block glues any line ending in '-' to the next line, swallowing the Standort ('Dorfen-Dorfen', 'Murnau-Murnau', 'Bruderwald-Bamberg'); (3) _dehyphen drops suspended hyphens ('Kinder- und' -> 'Kinderund'). Errors the deviation report cannot see because the DB holds the same misparse: 18102 name/town (plan: 'Psychosomatische Klinik Windach' / 'Windach a. Ammersee'), 57103 name/town (plan: 'ANregiomed Klinik Rothenburg o.d.T.' / 'Rothenburg o.d. Tauber'), 26204 'Kinderund'. 18872: an existing parse_error correction accepts 'Benedictus Krankenhaus' but the plan's name is 'Benedictus Krankenhaus Feldafing'. Also seen: website 16212 points at kbo-kinderzentrum-muenchen.de (belongs to 16211); 56402 website https://www.kh-nuernberg.martha-maria.de does not resolve.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 parse() splits name/Standort/Traeger by paragraph gaps; known-town guessing removed; regression tests on frozen real cells incl. the clipped-word, '-'-merge and suspended-hyphen cases; mutation-tested
- [ ] #2 tools/registry_build.py --report rerun on the live DB; every new deviation (the hidden misparses above among them) decided with first-hand evidence and applied or explained via apply_clinic_corrections / corrections rows after Ivan's go-ahead
- [ ] #3 18872 name and the 16212/56402 website fields corrected with evidence
<!-- AC:END -->
