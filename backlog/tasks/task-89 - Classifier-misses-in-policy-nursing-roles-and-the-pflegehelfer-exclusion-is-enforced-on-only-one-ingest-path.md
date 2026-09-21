---
id: TASK-89
title: >-
  Classifier misses in-policy nursing roles, and the pflegehelfer exclusion is
  enforced on only one ingest path
status: To Do
assignee: []
created_date: '2026-09-21 04:27'
labels: []
dependencies: []
ordinal: 89000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, findings M8 and M9.

Classifier misses (about 7 postings, plus precision):
- Hygienefachkraft is the recurring one: classify_role returns ('apn_experte','apn_experte:hygienefachkraft'), which is IN policy, yet 4 such rows across 36201, 76401 (x2) and 76301 are absent from the database although the adapter returns them. The drop happens AFTER classify and is unexplained -- tracing it is the valuable part of this task, because an in-policy row disappearing between adapter and database implicates the intake path, not the classifier.
- 'OP Leitung (m/w/d)' -> ('nicht_pflege','no_pflege_token'): the leitung rule requires a pflege token (18001).
- 'Onkologische Fachkraft (w/m/d)' -> ('nicht_pflege','no_pflege_token') despite section_labels carrying workarea 'Pflege- und Funktionsdienst' (18811, pflege_jobs/classify.py:90).
- patterns.json:77 'medizinische/?r? fachangestellte' does not match the inflected 'Medizinischen Fachangestellten', so MFA rows leak in (47701).

Policy applied inconsistently (about 12 rows, a decision rather than a bug): pflegehelfer is in patterns.json:347 excluded_role_classes and correctly drops 10+ rows (Sana Hof, Helios München West x2, GAP x3, InnKlinikum x2, Barmherzige, Günzburg, Ilmtal, Erler, Main-Spessart, Landsberg). But app/crawl.py:518 enforces it ONLY for seeded-adapter observations, so 27106 and 46401 currently hold open Pflegefachhelfer postings and 17101 holds open ausbildung rows. Same policy, opposite outcome depending on which code path the row took.

The pflegehelfer question is a product decision for Ivan before it is a code change: are Pflegehelfer/Pflegefachhelfer in scope for this board or not. Whichever way, it needs to be enforced in one place on every path.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The Hygienefachkraft drop is traced end to end and the real cause named with file:line -- the row is in-policy and the adapter returns it, so something between adapter and database discards it
- [ ] #2 'OP Leitung' and section_labels-rescued titles like 'Onkologische Fachkraft' classify correctly, and the MFA pattern matches its inflected forms
- [ ] #3 The pflegehelfer/ausbildung scope decision is recorded explicitly, then enforced at a single point that every ingest path passes through rather than only for seeded-adapter observations
- [ ] #4 Each classifier change is pinned by a test using the exact title strings from this task
<!-- AC:END -->
