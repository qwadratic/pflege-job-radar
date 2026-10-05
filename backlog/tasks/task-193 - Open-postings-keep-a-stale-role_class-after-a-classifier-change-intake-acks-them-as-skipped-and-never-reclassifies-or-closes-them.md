---
id: TASK-193
title: >-
  Open postings keep a stale role_class after a classifier change: intake acks
  them as skipped and never reclassifies or closes them
status: To Do
assignee: []
created_date: '2026-10-01 18:36'
labels:
  - db-quality
  - classifier
dependencies: []
priority: medium
ordinal: 190000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-01 by the TASK-184 adapter re-run (agent C): cli._process_rows acks rows that today classify as nicht_pflege or ausbildung as skipped, so their already stored open rows keep the old role_class and stay on the board. Example: barmherzige-regensburg.de 16 open rows are on the board today, stored role sonstige_pflege / apn_experte, today classify_role says nicht_pflege (no_pflege_token); last_seen froze on 2026-09-29. TASK-177 repaired 146 such rows with a one-off relabel script (data/relabel_task177_backfill.py); TASK-186 will do it again for Ausbildung, non-nursing and department. Each classifier change needs its own backfill, and nothing finds the rows that were never backfilled. Options for Ivan: a standing re-derivation step (replay classify_role over open postings after every classifier change, recorded in the corrections ledger) or a lint that fails when stored classes differ from classify_role over the open set.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ivan decides the mechanism; the open set shows 0 rows whose stored role_class differs from classify_role after the step runs
<!-- AC:END -->
