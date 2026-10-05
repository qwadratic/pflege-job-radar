---
id: TASK-184
title: >-
  Vacancy landscape: all hospitals (university separately, 10k-bed bands),
  Altenpflege pulled in, requirement/benefit combos, pay and extra conditions
  separately, title-vs-body reliability, no-description bucket
status: In Progress
assignee: []
created_date: '2026-10-01 16:29'
updated_date: '2026-10-01 16:44'
labels:
  - analysis
  - registry
dependencies: []
ordinal: 181000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-01: 'разбор всех остальных больниц, отдельно университетские, и каждые 10,000 коек + altenpflege подтянуть, сегментировать какие есть вакансии и комбинации требований и бенефитов + платежные условия отдельно + доп условия; также понять где тайтлы отражают / не отражают вакансии + вакансии без дескрипшена (без дескр всего лишь редфлаг, просто такие отдельно)'. Read-only analysis over the 3,870 open postings (3,736 board-visible): segments university / hospital / reha / social_linked / hospital_unlinked / stepstone_batch (TASK-165, 903, no description) / other_unlinked; hospitals cut into 10,000-bed cohorts by cumulative beds (largest first, grouped units = site or shared-board+town). Requirement/benefit/pay/condition atoms come from a tested section-aware regex extractor (the system's own structured fields are mostly empty: salary_min/max, quereinstieg, homeoffice, shift_night_weekend, fixed_term_months, salary_unit are NULL on all 3,870 rows; contract 26/3,870; employment_types 1,156). Title-vs-body judged per posting by LLM annotators on 2,794 postings with a description (24 batches, 20 anchors repeated in every batch to measure agreement). Altenpflege: sources/operators research, no DB writes. Working files: /home/exedev/.claude/jobs/663542db/tmp/landscape/.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Per-segment tables: vacancies, beds, vacancies per 1000 beds, description coverage; university clinics listed one by one; hospitals in 10,000-bed cohorts; every hospital unit in one sortable table
- [ ] #2 Requirement x benefit combinations per segment (top combos with counts), pay conditions and additional conditions reported separately, atoms precision-checked
- [ ] #3 Title-vs-body verdict per posting with issue taxonomy, rates by segment and by source/board, judge agreement on the anchor set, 30 verified examples; no-description postings reported as a separate red-flag bucket by board and reason
- [ ] #4 Altenpflege pull-in: sources and operators verified, what we already hold, proposal with the DB writes marked for Ivan's approval
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Dataset: all 3,870 open postings joined to registry (segment, unit, beds), board-visible = classes the board shows. 2. Segments + university one by one + 10,000-bed cohorts (largest first, units = same board host + town). 3. Requirement/benefit/pay/condition atoms: tested section-aware extractor (agent), combos per segment. 4. Title-vs-body: LLM judges over the 2,794 postings with a description (24 batches, 20 anchors in every batch), my own verification of a sample. 5. No-description bucket by board and cause; stale-open flag. 6. Altenpflege: sources/operators research (agent, read-only). 7. Artifact page + notes here. No DB writes in this task.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-01 interim finding (judge pilot, verified by reading DB rows): clinic 66103 Klinik am Ziegelberg Frauenklinik Aschaffenburg (30 beds, Plan-KH) has careers_url https://www.krankenpflegejobs24.de/klinik-am-ziegelberg-frauenklinik-aschaffenburg - an aggregator city page, not the clinic's own board. 181 open postings (4.7% of all open, 181 distinct postings, 1 employer name = the clinic, city = 'Aschaffenburg' for all, clinic_match_rule R0_board, first_seen 2026-09-11..09-30) are filed under it; judged samples are for other employers in Hessen etc. (Bad Orb, Dreieich, Erlensee, Seligenstadt). They inflate the hospital segment and the smallest 10,000-bed cohort (64.2 vs 30.5 vacancies per 1000 beds without them). Not fixed here: needs Ivan's approval for DB writes (retire the 181, clear careers_url of 66103 with a corrections row).

Same defect class, group boards filed under ONE Bavarian site by rule R0_board: karriere.ameos.eu - 55 postings all under AMEOS Klinikum St. Elisabeth Neuburg although the AMEOS portal lists the whole group (Swiss titles 'Diplomierte Pflegefachperson HF', 'Gut Neuhof Petershagen'). Full count comes from the judge's site_mismatch verdicts (see final notes).
<!-- SECTION:NOTES:END -->
