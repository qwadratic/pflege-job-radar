---
id: TASK-188
title: >-
  Hospital employers with open board postings but no registry clinic: 146
  postings (Helios 35, Klinik Hallerwiese 18, AWO Solequelle 13)
status: To Do
assignee: []
created_date: '2026-10-01 18:05'
labels:
  - db-quality
  - matcher
dependencies: []
priority: medium
ordinal: 185000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-01 by TASK-184 (segment hospital_unlinked). 146 board-visible open postings belong to employers that look like hospitals, clinic_id is NULL. Top employers (postings, board host): Helios (35, helios-gesundheit.de), Klinik Hallerwiese Nuernberg (18, jobs.diakoneo.de), AWO Klinik zur Solequelle (13, awo-omf.de), HELIOS Frankenwaldklinik Kronach (10), Klinik St. Hedwig Regensburg (8, barmherzige-bieten-zukunft.de), Diakoneo KdoeR (7), Klinikum St. Elisabeth Straubing GmbH (7), Klinikum Altmuehlfranken Gunzenhausen (5, coveto), Das Stillachhaus (3), Kliniken des Bezirks Oberbayern KU (3, kbo.de), HELIOS St. Elisabeth Bad Kissingen (3), Klinik Guenzburg (3), Klinikum St. Marien Amberg (3), Limes Schlossklinik Fuerstenhof (2). Some are group names on a group portal (employer "Helios" while the text names the hospital), some may have no registry row at all, some are the same clinic under another name. Related: TASK-185 (attribution of group boards). Data: TASK-184 working files report.json, key unlinked, and postings.json (segment hospital_unlinked).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every listed employer decided with first-hand evidence: relink to an existing clinic, new registry row, or not a hospital (reason recorded); the Matcher change, if any, comes from the decision, not before
- [ ] #2 Re-measured after the change: board-visible postings with a hospital-like employer and clinic_id NULL, before and after
<!-- AC:END -->
