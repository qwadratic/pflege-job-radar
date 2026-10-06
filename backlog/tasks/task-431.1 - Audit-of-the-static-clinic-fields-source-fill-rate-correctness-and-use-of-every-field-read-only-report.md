---
id: TASK-431.1
title: >-
  Audit of the static clinic fields: source, fill rate, correctness and use of
  every field (read-only report)
status: In Progress
assignee: []
created_date: '2026-10-06 07:20'
updated_date: '2026-10-06 08:12'
labels:
  - registry
  - data-quality
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 302000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
For every static field of a registry clinic (name, operator, traegerart, status, beds, day_places, size, versorgungsstufe, fachrichtungen, landkreis, regierungsbezirk, town, plz, lat/lon/geo_source, careers_url, ats_type, source, employer_id, website, photo, blurb): where does the value come from (Krankenhausplan PDF, Reha list, manual, crawl), how many of 651 are filled, a sample check of correctness against the source, and who reads it (code, API, UI, tests). plz is filled 0/651 and employer_id 0/651 today: say why. Result is a table in the task notes plus a list of fields that nobody reads or that are wrong. Also answer: per clinic, how complete is what we know (registry fields, vacancy fields we can extract from its board, membership of a group or operator) and how clean its postings are; propose one measure of that, without building it. No data changes in this task.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Table of all static clinic fields with source, fill rate of 651, sampled correctness and readers
- [ ] #2 List of unused or wrong fields with a recommendation (keep, fix, drop) per field
- [ ] #3 A proposal of one per-clinic completeness measure, with the inputs it needs
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Research done 2026-10-06 (read-only; data in the job tmp dir 431/audit: completeness.csv, plz_candidates.csv, deviations.csv, groups.json, ein_groups.json, readers.json; no DB, repo or backlog write).
Base: 651 = 409 KeZ (401 in plan 2026 + 8 nicht_mehr_im_plan), 229 RH (Reha, RHV 2024), 13 DK (hand list).
Correctness against sources: plan parse vs DB 401/401 (5 town differences explained by source_error corrections, 1 unexplained operator, 66103); RH 229/229; DK 13/13; 15 seeded raw PDF rows 15/15; own-site fetch of 18 clinics: website reachable 16/18, careers URL 200 in 16/18.
Nobody reads: employer_id (0/651, no writer, DDL only), plz (0/651, no writer: CLINIC_SPEC and the edge upsert lack it, sync_rhv_reha drops the PLZ column), updated_at (not in CLINIC_SPEC), geo_source, geo_name, career_profile (0 rows), jobs_per_100_beds, parse_quality (guard never fires); API payload only: lat, lon, source, board_shared; display only: day_places. lat/lon/geo_* are derived at runtime from town (app/data.py:290), not stored.
Wrong values: centroid wrong by 47-166 km for 6 clinics (57403, RH2229, RH2421, 18710, 18302, DK01), 17772 Flughafen Muenchen 31 km off; 16 clinics with a dead website host (RH1257, 17402, 56402 and 13 more); 7 single-posting careers URLs (16268, 47601, 77902 ...) and one trainee page (78071); 50 clinics with beds 0 labelled size S; versorgungsstufe '-' or '' in 286 instead of NULL; 22 sites listed twice; 8 town spelling pairs; leading-space fach tokens split wrongly for 77 RH clinics; 9 rows without source; blurb 'test' for 56407; traegerart oeffentlich for private GmbH 18803, 18804.
Groups: 379 raw / 366 normalised operators; 407 clinics (63 percent) in 122 multi-clinic groups. operator is a legal-entity key, not a brand-group key (only 62 percent of same-board pairs share an operator, 43 percent of same-operator pairs share a board). The plan prints 'EIN-Krankenhaus im Sinne des KHG' for 134 sites = 47 hospitals; the parser drops it; ein_groups.json holds it (right base for hospital-level beds).
Completeness prototype C = (0.40 R + 0.30 V + 0.10 G + 0.20 Q) / weights that apply; run 233 as base: complete >=0.85 287 (44 percent), partly 154 (24 percent), thin 210 (32 percent); mean 0.74. Thin because: board crawl issue 115, postings attributed to no clinic 56, no board 22. Partly: missing posting fields (employment type empty for 65 percent of postings) 80, operator vs board 44. Open for Ivan: weights, cut points, whether a healthy board without an in-scope vacancy counts as known.
Not verified: the PDF against the legal source, beds per site (KHV counts a hospital, the plan a KeZ site), ats_type beyond 18 fingerprints, PLZ for 268 KH chosen among several KHV sites.
Status: awaiting Ivan's decisions on the change list; nothing changed in any data.
<!-- SECTION:NOTES:END -->
