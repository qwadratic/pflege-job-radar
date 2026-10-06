---
id: TASK-431.9
title: >-
  Place match with a degree of confidence: a city in any spelling gives a PLZ,
  every version carries a weight, a posting matches a clinic with a confidence;
  experiments on the mirror in a separate branch
status: To Do
assignee: []
created_date: '2026-10-06 11:34'
labels:
  - registry
  - data-quality
  - provenance
  - matching
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 322000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06. The purpose of the PLZ work is attribution, not a query: the posting must be matched to the right clinic. A city mentioned in any spelling must in the end give a PLZ, so city and PLZ are one linked thing. Every version of the place of a posting (JSON-LD jobLocation, a PLZ field, a mention in the text, a stamp of the board or the employer headquarters) and of a clinic (imprint, directory site, postings) carries a weight, and the match between posting and clinic comes with a degree of confidence along that path. No closed yes/no even for matching: the same shape later for housing, working conditions and other claims, each with its kinds of verification and its confidence (TASK-441). START WITH THE CITY. Design constraint (Ivan): additive, touching no query that depends on the place. Checked 2026-10-06, dependents of the PLZ and city values today: pflege_jobs/geo.py in_bavaria(city, plz, region, towns) used by pflege_jobs/mechanics.py:99 and pflege_jobs/verify.py (placeable) and tools/reverify_and_clean.py; app/data.py:319 clinic_centroid reading clinics.plz; app/data.py JOB_COLS exposing posting city, plz, lat, lon; the clinic linking rules (link-clinics, rule R_jd_text has no town check, TASK-132); app/autopilot/* uses the CANDIDATE's PLZ and city, a different thing. The experiment adds new modules and tables read by nothing existing and changes none of the files above; moving link-clinics or in_bavaria onto the confidence is a separate decision after the numbers. Experiment, in a separate branch, offline on the mirror (TASK-197, no live site, no DB write): replay the adapters over the mirrored boards to get the place strings of postings; city spelling to canonical municipality to PLZ (gazetteer from the directory workbook, data/geo, the written clinic PLZ); weight per version with the board-stamp check (TASK-68, kbo.de); clinic side from the 649 written PLZ; match confidence per posting and clinic pair from PLZ equality, same municipality, same Kreis; then compare with the links in the DB today (read only): agree, disagree, unknown, and the disagreements listed with their evidence (e.g. 46110 jobs in Rehau and Ebensfeld linked to a Bamberg day clinic; 17205 postings with PLZ 83453). Relates to TASK-431.2 (place fit, research), TASK-431.8 (main PLZ of a clinic: one input of this match), TASK-441 (claim, evidence and confidence), TASK-132.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 List of every reader of posting and clinic place values with file and line, and the experiment changes none of them (checked on the diff)
- [ ] #2 Place claims for postings and clinics from the mirror: city spelling to municipality to PLZ, with a weight per kind of evidence and the board-stamp check
- [ ] #3 Confidence per posting and clinic pair, compared with the links in the DB today: counts of agree, disagree, unknown and the disagreements listed with their evidence
- [ ] #4 Report with the numbers to Ivan; adopting the confidence in link-clinics or in_bavaria is a separate decision and a separate task
<!-- AC:END -->
