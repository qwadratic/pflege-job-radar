---
id: TASK-431.7
title: >-
  PLZ for the 119 clinics whose PLZ is not written: review the 94 held matches,
  resolve the 25 big-city sites
status: To Do
assignee: []
created_date: '2026-10-06 10:24'
labels:
  - registry
  - data-quality
dependencies: []
parent_task_id: TASK-431
priority: low
ordinal: 316000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06: the unresolved clinics get their own worklist and a way to be resolved; only the reliable matches were written. Written on 2026-10-06 (pflege-clawl, 532 of 651, corrections rows with reason source_supplement): rhv_id 229, khv_domain 155, khv_only_site_in_municipality 135, dk_source 13. Open, worklist data/registry/plz_review.csv (119 rows): (a) 94 HELD: filled by weaker rules, khv_name_overlap 72 and khv_municipality_one_plz 22, with the candidate PLZ and the Krankenhausverzeichnis site; (b) 25 UNRESOLVED: 22 ambiguous (Muenchen 78 KHV sites and 35 PLZ, Nuernberg 17, Augsburg 12, Wuerzburg 9, Ingolstadt, Rosenheim, Landshut, Passau, Schweinfurt, Erding) and 3 without a KHV site in the municipality (17772 Muenchen-Flughafen = Oberding, 18302 Haag i.OB, 57707 Treuchtlingen). Method to try, cheapest first: for the held ones, accept a candidate when an independent source agrees (the clinic's own imprint PLZ, klinikradar, the modal PLZ of its linked postings; the audit found 213 of 224 modal postings equal and klinikradar 324 of 325); for the ambiguous big-city sites: street address from the clinic site imprint or the Krankenhausplan 2026 site line matched to the KHV site list, the modal PLZ of its linked postings as a tie-break; for the three without a site: the imprint. Each accepted value is written with tools/fill_clinic_plz.py --rules (a new rule name per evidence kind, e.g. imprint, posting_modal) so corrections rows say how it was found; no write without Ivan approving the counts.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each of the 119 rows has a verdict (accept, or leave unresolved with a named reason) and the evidence kind
- [ ] #2 Accepted values are written through tools/fill_clinic_plz.py with a rule per evidence kind; Ivan approves the exact counts first
- [ ] #3 The remaining unresolved clinics are listed with the reason in data/registry/plz_review.csv
<!-- AC:END -->
