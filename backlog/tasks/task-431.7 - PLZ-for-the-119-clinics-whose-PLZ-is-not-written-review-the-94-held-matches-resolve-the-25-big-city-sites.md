---
id: TASK-431.7
title: >-
  PLZ for the 119 clinics whose PLZ is not written: review the 94 held matches,
  resolve the 25 big-city sites
status: To Do
assignee: []
created_date: '2026-10-06 10:24'
updated_date: '2026-10-06 11:29'
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
- [x] #1 Each of the 119 rows has a verdict (accept, or leave unresolved with a named reason) and the evidence kind
- [x] #2 Accepted values are written through tools/fill_clinic_plz.py with a rule per evidence kind; Ivan approves the exact counts first
- [ ] #3 The remaining unresolved clinics are listed with the reason in data/registry/plz_review.csv
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-06 REVIEW DONE (read-only, pflege-clawl with a research agent; 767 pages and 216 sitemap probes on 132 hosts, at most 1 request per second per host, no retry after 403/429, no DB write). Verdicts: data/registry/plz_review.csv, 119 rows, each with the evidenced PLZ, the evidence kinds, confidence, source URL, a quote of at most 10 words and the date read. ACCEPT 117 (held 93, unresolved 24), LEAVE 2 (16257 Munich: unit planned only, no address; 26108 Landshut: two campuses 84034 / 84036, the imprint seat is not the main campus). Confidence high 109, medium 8 (17105, 17205, 37203, 56304, 76114, 16264, 16290, 17706), low 1. Of the 94 held candidates: confirmed 91, wrong 3 (16223 -> 80637, 16263 -> 81673, 16257 refuted); khv_name_overlap grabbed another operator's site on a shared token (Isar), khv_municipality_one_plz 22 of 22 right. Of the 25 unresolved: 24 resolved (imprint plus KHV street or name 18, imprint only 5, klinikradar plus host hospital 1). Rules for tools/fill_clinic_plz.py --verdicts (new option, red test first): imprint_khv_site 95, imprint 17, klinikradar 3, posting_modal 2. The value written is the evidenced PLZ, each correction row carries its own page URL, quote and date. Dry run on the live registry: fill 117, conflict 0. Findings for other tasks: (a) 18 confirmed rows have the right PLZ but the picked KHV site is another site of the same PLZ, in 5 another operator (17205 Salus, 18716 and 18776 Schoen Roseneck, 67208 and 67273 Heiligenfeld): do not reuse the held matches for beds or site fields (TASK-431.5, 441). (b) The KHV workbook files the airport clinics (Ort Muenchen-Flughafen, 85356) under the key of Erding: matching KHV Ort resolves Erding 17706 and Oberding 17772 without the web. (c) Postings as PLZ evidence: 47 clinics have postings with a PLZ, 39 agree, 3 split, 5 disagree; all 8 are wrong links or typos (46110 jobs in Rehau and Ebensfeld linked to a Bamberg day clinic): input for TASK-431.2, the posting link has no place veto. (d) Registry website broken: 16239 lubos-klinken.de (real lubos-kliniken.de), 17401 helios-gesundheit (no TLD). (e) 6 accepted rows are plan status Bedarf festgestellt (16106, 17306, 17308, 57506, 17706, 57707): the PLZ is the planned site or host, not proof the unit runs. (f) 16290 LMU accepted as seat 81377 (Grosshadern, medium); 16107 ZPG has two addresses, 85049 inpatient (accepted) and 85051 day clinic. Not checked: hosts that blocked (helios 403 not circumvented, kinderzentrum.de 429), no Deutsche Post check, the one-PLZ-per-municipality count comes from GeoNames. WRITE WAITS for Ivan approving the exact counts: 117 PLZ by rule (95 / 17 / 3 / 2), 2 left.

2026-10-06 PLZ WRITTEN: Ivan approved all 117 ("117 можно включать"). `fill_clinic_plz.py --verdicts data/registry/plz_review.csv --apply --by pflege-clawl`: 117 corrections applied (rules imprint_khv_site 95, imprint 17, klinikradar 3, posting_modal 2), backup backups/fill_clinic_plz_20261006T112450Z.json. Read back through the Supabase connector: 649 of 651 clinics have a PLZ, 649 correction rows with reason source_supplement (532 first fill + 117), 16223 = 80637 and 16263 = 81673 (the evidence beat the weaker rule's candidates), 2 without a PLZ: 16257 (planned unit, no address) and 26108 (Landshut, two campuses 84034 Mitte / 84036 Achdorf, no single PLZ). RE-CHECK PLANNED, Ivan: "the medium ones need a separate look". Rows written with thinner evidence: 17105, 37203, 17706 (klinikradar plus the KHV site, no own page of the unit: kinderzentrum.de answered 429, Sana pages carry Cham only, kbo-iak.de lists no Erding unit), 17205 and 56304 (PLZ right by imprint, the picked KHV site is another site of the same PLZ), 76114 (imprint, klinikradar and KHV say 86156, 4 of 5 linked postings say 86159: is 86159 a second site?), 16264 (imprint = KHV site 81377; one posting 81677 is a wrong link), 16290 LMU (seat PLZ 81377 of a two-campus clinic). Who and what: pflege-clawl checks 76114 (one posting), 17706 (kbo lists the unit?) and the three klinikradar-only rows when the sites answer; Ivan decides what the PLZ of a multi-campus clinic means (16290, 26108). Task stays open until then.

2026-10-06 Ivan on the open policy question (multi-campus clinics 16290, 26108, and PLZ in general): a clinic can have several PLZ (OR); the main PLZ comes from a simple decision tree (own address, directory site, weight by number of fresh postings naming the PLZ, check that it is the clinic's and not a board PLZ, proximity inside a municipality or Kreis then the main one), and the extended field holds all PLZ with reasons and links. Moved to the follow-up subtask of TASK-431 created for it; 26108 and 16290 are decided there, 76114, 17706 and the klinikradar-only rows stay with pflege-clawl here.
<!-- SECTION:NOTES:END -->
