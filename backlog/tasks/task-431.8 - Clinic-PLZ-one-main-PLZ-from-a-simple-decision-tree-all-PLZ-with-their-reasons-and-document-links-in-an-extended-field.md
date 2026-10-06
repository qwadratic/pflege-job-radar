---
id: TASK-431.8
title: >-
  Clinic PLZ: one main PLZ from a simple decision tree, all PLZ with their
  reasons and chain to the mirror answered by a query
status: To Do
assignee: []
created_date: '2026-10-06 11:29'
updated_date: '2026-10-06 11:35'
labels:
  - registry
  - data-quality
  - provenance
dependencies: []
parent_task_id: TASK-431
priority: medium
ordinal: 321000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06. A clinic can have several PLZ (several sites), so the rule is OR, not one value. (1) No separate "extended PLZ" field (Ivan's intuition): the tables are built so that a query returns every PLZ of a clinic with its provenance, the chain from the claim down to the mirror: PLZ, kind of evidence, source page or document, quote, date read, the mirror page it was read from (TASK-197), the rule or model that decided. The evidence rows are the catalogue of TASK-441; this task is its first case. (2) `plz` stays ONE value, the main PLZ, produced by a simple decision tree that always ends at one PLZ or at a named no-PLZ reason; it is the chosen claim over those evidence rows. (3) Ingredients of the tree: the clinic's own imprint or address; the Krankenhausverzeichnis site; a weight that grows with the number of FRESH postings that name the PLZ; a check that the PLZ belongs to the clinic and not to the board or the employer headquarters stamped onto postings (TASK-68: kbo.de stamps its Munich address on every site posting; a PLZ that repeats on postings regardless of each posting's own location is a board PLZ); administrative proximity: PLZ inside one municipality or Kreis count as close, and then the main one is taken (the seat, or the larger campus); where several remain the main PLZ is the seat and the others are returned by the query as further PLZ of the clinic. Test cases from the review of TASK-431.7: 16290 LMU (two campuses, seat 81377 Grosshadern), 26108 Landshut (84034 Mitte and 84036 Achdorf), 76114 Augsburg (86156 by imprint, 86159 on 4 of 5 postings), 17205 (postings carry 83453, a typo, not a PLZ of Bad Reichenhall), 46110 (jobs in Rehau and Ebensfeld wrongly linked to a Bamberg day clinic). This answers the open question "what does the PLZ of a multi-campus clinic mean" of TASK-431.7. The 532 + 117 corrections rows already written carry URL, quote and date and are the first evidence rows. Open for the design: whether the query is a view over the evidence rows plus corrections, or a table; the mirror link needs the mirror page to be addressable by a key (board id, URL, recorded time).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The tree is written down: order of the branches, the weight by fresh postings, the board-PLZ check, the proximity rule, and how each clinic ends at one PLZ or a named no-PLZ reason
- [ ] #2 Run over all 651 clinics: counts per branch and the list of clinics whose main PLZ would change against the written value; Ivan approves before any write
- [ ] #3 One query returns every PLZ of a clinic with its chain down to the mirror page, with no separate extended field; the schema is consistent with the corrections table (TASK-180) and the evidence catalogue (TASK-441)
- [ ] #4 Red tests first for the board-stamped PLZ (kbo.de), the two-campus clinic, and two close PLZ inside one municipality
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-06 Ivan: the aim is attribution (matching the posting to the right clinic), not the PLZ query; a city in any spelling must also end at a PLZ, so city and PLZ are one linked thing, and every version of a place carries a weight. The main PLZ of this task is one input of the confidence match of TASK-431.9, which is done first as an additive experiment on the mirror in a separate branch; the tree here stays simple.
<!-- SECTION:NOTES:END -->
