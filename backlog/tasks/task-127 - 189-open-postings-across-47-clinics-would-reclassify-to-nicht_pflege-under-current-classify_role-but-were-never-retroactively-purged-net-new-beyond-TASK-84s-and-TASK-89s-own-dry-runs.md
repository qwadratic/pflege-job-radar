---
id: TASK-127
title: >-
  189 open postings across 47 clinics would reclassify to nicht_pflege under
  current classify_role but were never retroactively purged (net-new beyond
  TASK-84's and TASK-89's own dry-runs)
status: To Do
assignee: []
created_date: '2026-09-23 08:59'
labels: []
dependencies: []
priority: medium
ordinal: 127000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Live-recompute classify_role against every currently open posting (same method as TASK-84 AC#4/TASK-89 AC#3's dry-runs) and confirm the 189-row/47-clinic net-new set is still current (data drifts daily)
- [ ] #2 Investigate Klinikum Kempten (76301) and Klinik Oberstdorf (78002) specifically: several open postings have a bare NUMBER as their title ('1531', '856', '2128', '2245', '2324', '2619', ...) -- looks like a live title-extraction bug (an internal id leaking into the title field), not just a stale-classification case; root-cause before folding these into a blind reclassification purge
- [ ] #3 Apply (or hand Ivan a ready SQL command for) the confirmed non-Kempten/Oberstdorf portion of the purge, same status='retired' shape as TASK-84/TASK-89's pending purges -- this session's key has no write access to pflege_jobs.postings (confirmed live, 401/42501)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Found live 2026-09-23 while building TASK-89 AC#3's dry-run (backups/task_stale_reclass_net_new_20260923.json holds the 189 rows). Method: fetched all 3127 currently-open v_postings rows, recomputed classify_role(title, hauptberuf, offer_kind) for each, flagged where the new role_class disagrees with the stored one AND the new role_class is nicht_pflege. Overlap check against TASK-84's own pending 81-row dry-run (backups/task84_purge_dryrun_ids_20260922T024753Z.json): 80 rows already covered there. Against TASK-89's 37-row dry-run: 13 already covered there. This task is the remaining 282-80-13=189, deliberately not purged as part of either of those two narrower tasks to avoid scope creep. Confirms several are TASK-126's speculative_application fix catching real currently-open fake postings (e.g. posting 6314 LMU 'Initiativbewerbung als Pflegefachkraft (m/w/d)', 6235 Krankenhaus Rummelsberg 'Initiativbewerbung Pflegebereich (m/w/d)'). The Klinikum Kempten/Klinik Oberstdorf numeric-title cluster (postings 5826/6018/6019/6020/6022/6023/6025/5848 and likely more at those two clinics) is flagged separately because it does not look like ordinary classifier drift -- a title that is literally just a number is a different, likely-live bug worth its own root-cause pass before any of those specific rows are purged.
<!-- SECTION:NOTES:END -->
