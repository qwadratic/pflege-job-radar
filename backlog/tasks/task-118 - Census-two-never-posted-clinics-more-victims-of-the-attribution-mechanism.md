---
id: TASK-118
title: >-
  Census: two never-posted clinics (Weilheim 19002, BKH Aschaffenburg 66105)
  have a real posting on their own shared board that lands on a sibling
  clinic_id instead -- new named victims of the TASK-81/96 attribution mechanism
status: To Do
assignee: []
created_date: '2026-09-22 18:28'
updated_date: '2026-09-22 18:28'
labels: []
dependencies: []
ordinal: 118000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Same 2026-09-22 never-posted census (143/407 registry clinics, zero pflege_jobs.v_postings rows ever). Cross-referenced the 143 against data/app.sqlite crawl_issues kind='city' (the per-posting town-mismatch warning: 'page says X, stored Y') to look for the TASK-96 shape -- a real posting exists but a town/board gate refuses to attach it to the correct clinic. Two concrete, previously-unnamed hits: (1) clinic 19002 Krankenhaus Weilheim (town=Weilheim, careers_url meinkrankenhaus2030.de/karriere/stellenboerse, shared with 19001 Krankenhaus Schongau). crawl_issues logged 'page says Weilheim (None), stored Schongau' for source_url .../stellenanzeige-operations-technischen-assistent-w/m/d-in-vollzeit. Checked live in pflege_jobs.v_postings (rest_get): that exact source_url IS present as posting_id 6268, open, but city='Schongau' and clinic_id=19001 -- the JSON-LD page itself names Weilheim, but the row was stamped with the shared board's other/seed clinic's town and matched to 19001 instead of 19002. Same for its sibling posting_id 6267 (title differs slightly, same board). This is TASK-81 mechanism #3's shape (app/crawl.py:545 seed-inherited city, pflege_jobs/registry.py:238 R0_board_name/R0_board_town matching straight back to the seed) but neither 19001 nor 19002 nor meinkrankenhaus2030.de is named in TASK-81's current mechanism list. (2) clinic 66105 Psychiatrische Klinik Aschaffenburg des BKH Lohr am Main (town=Aschaffenburg, careers_url karriere.bezirkskrankenhaus-lohr.de, ats_type=concludis, shared with 66104 the Lohr am Main main site). crawl_issues logged 'page says Lohr (97816), stored Aschaffenburg' for source_url .../jobs/pflegefachkraft-m-w-d/. Checked live: that exact source_url is posting_id 10076, open, title literally 'Pflegefachkraft (m/w/d) in Lohr a.Main, Aschaffenburg oder für unseren Springerpool' (an explicitly multi-site posting spanning both towns) -- but it landed entirely on clinic_id=66104 (Lohr), city='Aschaffenburg' kept as the display city while match went to the wrong site. 66105 (the Aschaffenburg satellite) has never gotten a single posting even though this job explicitly lists it as a work location. Neither clinic nor bezirkskrankenhaus-lohr.de is named in TASK-96's current scope (which covers KJF Klinik Hochried/18006, a different registry pair). Both cases are the same underlying pattern TASK-96 documents (a shared/multi-site board's town signal picks one registry clinic and the matcher will not also credit the other real, named site) but are two fresh, concrete instances the existing tickets don't yet list -- filed separately so TASK-81/96's eventual fix has more than one example to generalize against, and so this pair doesn't silently stay at 0 postings if TASK-81/96 close without having seen them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 66105 (Aschaffenburg) and 66104 (Lohr am Main)'s shared concludis board recognizes explicitly multi-site postings (title names both towns) and either links the posting to both clinic_ids or picks correctly per the town actually meant, instead of defaulting to one site every time
- [ ] #2 TASK-81 and TASK-96 are updated to reference these two new cases (19001/19002 and 66104/66105) alongside their existing named examples, so a fix validated only against KJF Hochried or the original 21-clinic list doesn't silently miss this shape
- [ ] #3 Re-run confirms 19002 and 66105 each have at least the posting(s) identified here (6267/6268 and 10076 respectively) attributed to their own clinic_id, not just the sibling's
- [ ] #4 19002 (Weilheim) and 19001 (Schongau)'s shared meinkrankenhaus2030.de board postings are re-matched with a fix that lets 19002 keep postings whose JSON-LD city says Weilheim even though the seed/board's stored town is Schongau, without breaking 19001's own correct matches
<!-- AC:END -->
