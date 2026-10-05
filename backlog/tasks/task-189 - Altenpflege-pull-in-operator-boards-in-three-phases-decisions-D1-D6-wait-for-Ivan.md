---
id: TASK-189
title: >-
  Altenpflege pull-in: operator boards in three phases, decisions D1-D6 wait for
  Ivan
status: To Do
assignee: []
created_date: '2026-10-01 18:05'
labels:
  - altenpflege
  - registry
dependencies: []
priority: medium
ordinal: 186000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Result of the TASK-184 Altenpflege research (2026-10-01, read-only, notes in TASK-184 and landscape/altenpflege_notes.md). No open complete Bavarian facility list exists (AOK and vdek forbid commercial use, Pflegefinder has no export); OSM gives 1,821 rows (ODbL), 38% of the 2,146 Pflegeheime of Pflegestatistik 2023. 22 operators verified, 1,664 Bavarian vacancies with a nursing-style title on 18 operator boards; the DB holds 599 Altenpflege-looking open postings, 540 of them from the 2026-09-24 StepStone batch (TASK-165), none with a description. Phase 1 needs no new code (b-ite and softgarden boards: Diakonie Muenchen 107, Victors Group 139, Augustinum 77, MUENCHENSTIFT 38, Paritaetische Altenhilfe 24 = 385 jobs); phase 2 BRK 831, Korian 260 (new Jibe adapter), AWO 623 (new list adapter); phase 3 small boards. Decisions for Ivan: D1 scope (postings only / registry rows for crawled operators only / full registry about 4,300 rows), D2 StepStone batch after the boards are live, D3 list source and licence (operator sites vs OSM ODbL), D4 row shape (id prefix AP, beds, day_places, no PLZ column), D5 whether Altenpflege rows are funnel and autopilot targets, D6 regional pool portals (allgaeuer-jobs.de, oberland-jobs.de) as career sites. Side effect to design around: every clinics row becomes a crawl target and a row without a routable board gets fetch=firecrawl (app/targets.py, app/data.py:282-284).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ivan decides D1-D6; nothing is written before
- [ ] #2 Phase 1 rows (clinics with careers_url and ats_type) inserted through the corrections-ledger tools after an offline Matcher replay of the crawled postings against the proposed rows shows 0 wrong attachments
- [ ] #3 Korian Jibe adapter and AWO list adapter built red-green against the board as oracle (tests/adapter_contract.py)
<!-- AC:END -->
