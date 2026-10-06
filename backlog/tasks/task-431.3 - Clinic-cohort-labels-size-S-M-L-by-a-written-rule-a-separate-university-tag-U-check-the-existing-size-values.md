---
id: TASK-431.3
title: >-
  Clinic cohort labels: size S/M/L by a written rule, a separate university tag
  U, check the existing size values
status: In Progress
assignee: []
created_date: '2026-10-06 07:20'
updated_date: '2026-10-06 14:04'
labels:
  - registry
  - data-quality
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 304000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Registry today: size S 309, M 247, L 70, XL 12, empty 13; status HS-Klinik 7 (university hospitals); versorgungsstufe filled for 409 only. Ivan wants cohorts S, M, L by clinic size and a separate tag U for university hospitals (they are a different kind of clinic and should be L or carry U on top). Write the rule (which field and thresholds: beds, versorgungsstufe, status), check the 638 existing size values against it and list the differences, decide what XL means in relation to L, add the U tag from status HS-Klinik and a reviewed list (Bavaria has five university hospital sites plus the Augsburg one that became a university hospital; verify from the source, do not assume). Keep it consistent with TASK-184 (university separately, 10k-bed bands) and TASK-143 (beds correctness). The 13 clinics without beds get a label or an explicit named reason.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Written rule for S/M/L (and XL if kept) and for U, in docs or the registry module
- [ ] #2 List of clinics whose current size differs from the rule, with counts per cohort before and after
- [ ] #3 U tag set for every university hospital and tested; DB writes only after Ivan approves the exact counts
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Research done 2026-10-06 (read-only; tables in the job tmp dir 431/cohort/report.md).
Current rule: size is not stored; it is computed per snapshot in app/data.py:287 via size_bucket() (app/data.py:202) from taxonomy.json size_buckets: S 0-99, M 100-299, L 300-799, XL >=800, beds NULL gives no size; beds only (versorgungsstufe, status, type play no part). Threshold copies: data/registry/taxonomy.json, app/fallback/taxonomy.json:5, _DEFAULT_SIZES app/data.py:210, hand-copied sizeOf in web/pro.template.html:518 and web/pro.html:518, docs/overview.md:142. Violations of its own rule: 0 of 638. Defect: the 50 clinics with beds 0 are labelled S (309 S = 259 + 50). The 13 empty are DK01..DK13 (Diakoneo social, beds NULL).
Beds quality: acute beds are planned beds of the Krankenhausplan 2026 (repo parser vs registry: 0 differences on 401 rows); Reha beds equal RHV 2024 for 229/229; TASK-143 still has no error rate. 50 zero-bed clinics: 42 day-place-only (Plan-KH 40, Vertrags-KH 2) + 8 Bedarfsfeststellung; they carry 19 postings. 1-29 beds: 68 clinics, 30-49: 70; 138 of 259 real S sit below the 50-bed floor of the ratio metric. Destatis KHV 2024 cross-check (fuzzy): 176 Plan-KH pairs, 119 within 10 percent, 28 registry below 0.8 x KHV (e.g. Ingolstadt 798 vs 1103); 20 would change bucket; mostly matching artefacts.
Type matters more than size: postings per 100 beds (beds >=50): acute 2.71, Reha 0.42 (6.5 times lower); share of clinics with any posting 62 percent acute vs 27 percent Reha; eta-squared of the ratio by type 0.132, by S/M/L 0.006. 36 percent of linked postings are group-board stamps (R0), 60 percent in S: S metrics mostly measure board structure. 11 operator groups hold beds on one main site with 0-bed day clinics elsewhere (kbo-IAK, Sozialstiftung Bamberg, medbo ...), not a data error.
Recommended rule: S <100 (259), M 100-299 (247), L >=300 (82; 76 without U), XL folded into L (only the 12 XL relabel), U on top, a named reason for clinics without beds (63): no_bed_concept 13 (Diakoneo social, 28 postings), day_places_only 42 (14 postings), planned_only 8 (5 postings); the 8 nicht_mehr_im_plan keep their size by beds. Alternatives: keep XL (S 259 / M 247 / L 70 / XL 12); 150/400 (353/180/55); 200/500 (426/124/38); 50/200 (138/288/162). T1 (current thresholds, XL folded) separates acute clinics best (eta-squared 0.356 vs 0.347 / 0.332 / 0.326). Keep acute vs Reha as a second axis (status); never pool Reha and acute in a ratio.
U list: the 7 HS-Klinik rows are 6 institutions per Art. 1 BayUniKlinG (Augsburg, Erlangen, LMU, TUM, Regensburg, Wuerzburg): 16290 LMU, 76190 UK Augsburg, 66390 Wuerzburg, 56290 Erlangen, 16291 TUM MRI, 36290 Regensburg, 16292 DHM (part of TUM Klinikum since 2024-08-01). Krankenhausplan 2026 section 1 (p. 244-250) lists exactly these 7. Status wrongly includes: none; misses by law: none. U independent of size: 6 become L+U, DHM stays M+U. Academic candidates (not U by law; optional second tag A, Ivan decides): 36209 Bezirksklinikum Regensburg, 76114 BKH Augsburg, 56401 and 56410 Klinikum Nuernberg (sources fetched), 66305, 76111, 46201 (snippets only). 32 clinics call themselves Lehrkrankenhaus in generated blurbs (unverified).
Metrics by cohort (T1 + U): postings per 100 beds / share with a posting / share of boards with a posting: S 1.84 / 34 / 43; M 1.69 / 56 / 58; L 2.44 / 85 / 94; L without U 2.19 / 84 / 94; U 3.62 / 100 / 100; day clinics n/a / 19 / 26; social n/a / 92. Acute-only S/M/L 2.81 / 2.49 / 2.63; Reha-only 0.74 / 0.36 / 0.38.
Not verified: gesetze-bayern.de answered 503 (statute read via a mirror); 1114 postings have no clinic_id so all ratios are lower bounds; group-board attribution (TASK-185) unresolved and inflates S per-site counts.
Status: awaiting Ivan's decisions (XL, U vs size, tag A); no data changed.

2026-10-06 DECIDED by pflege-clawl after the impact check (Ivan: "tails 1 and 3 yes, re-check them; decide yourself when sure, look at the impact"). Local data: registry fixture of 2026-10-06 and open postings per clinic of the audit (2 338 open on registry clinics). (a) XL stays folded into L (SML is Ivan's own scheme). Impact: XL (800+ beds) is 12 clinics with 508 open postings (21.7 percent), 3.44 open per 100 beds; L 300-799 is 70 clinics with 720 open (30.8 percent), 2.22 per 100 beds. The density differs by 55 percent, but statistics are per 100 beds and read the bed number, not the label, so nothing is lost; an 800+ cut stays available from beds if a view needs it. (b) University tag U stays independent of size. Impact: 7 clinics (342 open postings, 14.6 percent), six L and one M (197 beds); a size-only label would hide the M one. (c) Tag A (academic teaching hospital) is not introduced: no source in the registry, no consumer; revisit with TASK-441.
<!-- SECTION:NOTES:END -->
