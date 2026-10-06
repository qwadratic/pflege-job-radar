---
id: doc-5
title: Deferred candidates register
type: other
created_date: '2026-10-06 14:33'
updated_date: '2026-10-06 14:34'
tags:
  - registry
  - review
---
# Deferred candidates register

Findings that were measured and judged worth keeping, but not adopted now (Ivan, 2026-10-06: "this knowledge must be kept somewhere as candidates for later adoption"). Each row holds the agent's comment with the measured impact, the date of that comment, the date of the next review, and what would make it worth adopting.

How it is used: `.venv/bin/python tools/deferred_due.py` lists the rows whose next review date has come (also checked at the start of a session of the numbering lane and when a related task closes). At a review the impact is measured again, then the row is adopted (a task is created and the row says which), re-dated with a new comment, or dropped with the reason. The table is read by tools/deferred_due.py: keep the columns and ISO dates.

| id | candidate | source | comment and impact | comment date | by | next review | adopt when |
|---|---|---|---|---|---|---|---|
| D1 | Size of a Vertrags-KH row is the size of its whole site | TASK-431.5, TASK-431.3 | Right in principle. Verified for 4 sites: 6 rows change (S 259 to 254, M 247 to 250, L 82 to 84). 16 further Vertrags-KH rows are unverified and no site key exists. | 2026-10-06 | pflege-clawl | 2026-11-06 | site groups exist (TASK-441 inventory) |
| D2 | Weiden 36302 beds 0 to 32 as evidence, not as an override | TASK-431.5 | Operator page: 32 beds in operation since 2026-04 (medbo, article of 2026-07-10). Plan of 01.01.2026: approved 0 beds and 12 places, target 32 and 33; the places differ (18 against 33), so the match is medium. Impact: 1 clinic, 1 open posting, S 259 to 260, None 63 to 62. | 2026-10-06 | pflege-clawl | 2026-11-06 | the catalogue of TASK-441 holds beds_reported, or the next plan edition lists the beds |
| D3 | Planned beds and places ("in Planung") as evidence | TASK-431.5 | The plan parser reads two of the plan columns; planned values exist for 153 of 401 rows and 82 differ from the approved beds (sum +1117, e.g. Klinikum Ingolstadt 798 to 1109). 8 Bedarfsfeststellung clinics carry only planned numbers. No consumer today. | 2026-10-06 | pflege-clawl | 2026-11-06 | the catalogue of TASK-441 exists |
| D4 | Diakoneo places as places_social evidence | TASK-431.5 | 13 clinics with beds NULL; 11 have a place count on the operator page (5 stated, 6 derived as x + 2y). Size stays no_bed_concept. No consumer today. | 2026-10-06 | pflege-clawl | 2026-11-06 | the catalogue of TASK-441 exists |
| D5 | A separate XL label (800+ beds) | TASK-431.3 | 12 clinics, 508 of 2338 open postings (21.7 percent), 3.44 open per 100 beds against 2.22 for L 300 to 799. Folded into L; statistics per 100 beds read the bed number, so nothing is lost. | 2026-10-06 | pflege-clawl | 2026-11-20 | a published statistic by size shows the L bucket hiding XL |
| D6 | Tag A for academic teaching hospitals | TASK-431.3 | No registry source and no consumer. | 2026-10-06 | pflege-clawl | 2026-12-06 | candidate matching by clinic type (TASK-431.6) needs it |
| D7 | Upper completeness cut 0.85 against 0.90 | TASK-431.1 | Complete, partly, thin: 287, 154, 210 at cuts 0.85 and 0.65; 196, 245, 210 at 0.90 and 0.65. Thin stays between 202 and 219 for every alternative; no action hangs on complete against partly. | 2026-10-06 | pflege-clawl | 2026-12-06 | an action is attached to complete against partly |
| D8 | The place confidence match replaces or informs the link rules | TASK-431.9 | Experiment on the mirror: of 3834 linked postings 278 disagree on place (7.3 percent) and 420 are unknown. Not ready to replace the rules: it needs a labelled sample, a site level and an answer for unknown. | 2026-10-06 | pflege-clawl | 2026-11-20 | fix/place-links is merged and a labelled sample exists |
| D9 | Site level of the place match | TASK-431.9 | 516 of 651 clinics share a municipality and 485 a PLZ; 702 of 1302 unlinked postings point at several clinics. Place alone cannot decide the site. | 2026-10-06 | pflege-clawl | 2026-11-20 | D8 is taken up |
| D10 | Clean the PLZ fields at the adapters | TASK-431.9 | Only 40 percent of observations carry a five-digit PLZ and 578 PLZ values are junk; the city decides far more often than the PLZ. | 2026-10-06 | pflege-clawl | 2026-11-06 | after fix/place-links, before D8 |
