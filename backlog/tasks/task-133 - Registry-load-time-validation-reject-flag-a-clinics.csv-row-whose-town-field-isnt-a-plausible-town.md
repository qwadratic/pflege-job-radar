---
id: TASK-133
title: >-
  Registry load-time validation: reject/flag a clinics.csv row whose town field
  isn't a plausible town
status: To Do
assignee: []
created_date: '2026-09-23 14:43'
updated_date: '2026-09-25 00:10'
labels:
  - db-quality
dependencies: []
priority: low
type: task
ordinal: 133000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-131 found 28 clinics.csv rows (parse_quality='partial') whose town field is not a real town at all -- a legal-suffix fragment ('Co. KG', 'GmbH & Co. KG'), a generic clinic-vocabulary word ('Kliniken', 'Fachklinik'), an operator/parent-company name, a Regierungsbezirk, or a person's name. This broke Matcher's town-stripping (TASK-101: clinic 18872's garbage town field let 'feldafing' survive in its operator tokens, false-matching 48 real postings) before TASK-131's parse_quality guard shipped as a stopgap.\n\nparse_quality='partial' already flags SOME of these, but per TASK-131's own scan two rows (67274, 77672) had a garbled name/operator with a CORRECT town field, and one (47503) was flagged partial but reads clean -- the parse_quality label and actual data-shape don't perfectly correlate. A structural, ongoing safeguard (checked whenever clinics.csv is loaded/rebuilt, not just this one cleanup pass) would catch a future bad PDF-extraction row before it reaches Matcher at all, rather than relying on someone noticing the downstream symptom again.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A validation check (run in pflege_jobs/registry.py's Matcher.__init__, or a standalone lint invoked wherever clinics.csv is loaded/regenerated) flags any row whose town field contains legal-suffix tokens (gmbh/kg/co/ag), is a KINDS word (klinik/kliniken/fachklinik/...), or fails a plausibility check some other way
- [ ] #2 Flagged rows are logged/reported (not silently dropped -- a missing clinic is worse than a corrupted one per this project's no-safety-nets rule) at CSV load or at a dedicated lint command's run, with the specific clinic_id and offending field value
- [ ] #3 Red-green test: a synthetic clinics.csv row with a garbage town field is flagged by the check; a normal row is not
<!-- AC:END -->
