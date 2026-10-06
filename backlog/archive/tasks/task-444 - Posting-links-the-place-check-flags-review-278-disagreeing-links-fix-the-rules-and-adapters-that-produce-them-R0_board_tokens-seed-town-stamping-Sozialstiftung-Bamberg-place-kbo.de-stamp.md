---
id: TASK-444
title: >-
  Posting links the place check flags: review 278 disagreeing links, fix the
  rules and adapters that produce them (R0_board_tokens, seed-town stamping,
  Sozialstiftung Bamberg place, kbo.de stamp)
status: To Do
assignee: []
created_date: '2026-10-06 12:37'
updated_date: '2026-10-06 12:44'
labels:
  - crawler-coverage
  - db-quality
  - matching
dependencies: []
priority: medium
ordinal: 323000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the experiment of TASK-431.9 (branch exp/place-confidence, report and disagreements.csv in the job directory of pflege-clawl). 278 of 3 834 linked postings (7.3 percent) disagree with their clinic on place; 126 of them are open postings. By rule: R0_board 121, R1_exact 65, R0_board_town 26, R6_ambiguous_sites 24, R0_board_tokens 18 (all 18), R3_tokens 13, R2_operator_town 4, R_jd_text 3. Defects to fix at the source: (1) R0_board_tokens is wrong 18 of 18 times (Helios Erlenbach for Dachau and Markt Indersdorf postings); (2) R0_board links aggregator and multi-employer board postings from elsewhere in Germany to one seed clinic (166 links on 66103, 18501, 56103) and R0_board_town agrees with the seed town by construction (25 open ANregiomed postings from Rothenburg on the Ansbach clinic); (3) R1_exact has no veto for places that are not registry towns (47 of 65 on one Aschaffenburg clinic fed by an aggregator); (4) Sozialstiftung Bamberg: stored city Bamberg, the adapter reads Forchheim (24 R6 links to 46101 and 5 R3 links to RH1611 probably wrong); (5) the kbo.de head-office stamp (80538 Muenchen) still sits on 5 stored rows. Any correction of links or rows is a database write: exact counts to Ivan first (corrections table, TASK-180). No safety nets: fix the cause, do not add a place guard that silently drops postings; unknown (posting without own place) stays unknown.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The 278 disagreeing links reviewed: each is confirmed wrong (with the better clinic) or confirmed right, with the evidence
- [ ] #2 Each of the five defects fixed at its source with a red test first on the mirror, or recorded with the reason it is not a defect
- [ ] #3 Corrections to links and rows written only after Ivan approves the exact counts; before and after counts of disagreeing links by rule
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-06 Folded into TASK-431.9 on Ivan's word ("the executor and fixing the data are one task"); acceptance criteria and defect list moved there. Archived.
<!-- SECTION:NOTES:END -->
