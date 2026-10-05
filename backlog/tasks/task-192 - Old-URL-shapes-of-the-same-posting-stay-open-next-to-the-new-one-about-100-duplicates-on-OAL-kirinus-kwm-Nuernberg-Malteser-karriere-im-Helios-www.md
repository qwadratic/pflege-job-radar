---
id: TASK-192
title: >-
  Old URL shapes of the same posting stay open next to the new one: about 100
  duplicates on OAL, kirinus, kwm, Nuernberg, Malteser, karriere-im, Helios www
status: To Do
assignee: []
created_date: '2026-10-01 18:36'
labels:
  - db-quality
  - crawler
dependencies: []
priority: medium
ordinal: 189000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-01 by the TASK-184 adapter re-run (agent C and A reports, numbers from their diff files in /home/exedev/.claude/jobs/663542db/tmp/rerun/). A site changes the URL shape or host of a job and the old row stays open next to the new one: kliniken-oal-kf.de 13 rows with the old shape /stellenangebote/s<uuid>--<slug> (same UUIDs as 13 of the 17 current /stellenangebote/job/<uuid>, 7 with mangled titles); karriereportal.kirinus.de 7 legacy rows plus 7 second-URL-form rows (7 jobs stored three times, 21 open rows); kwm-klinikum.de 12 rows under the old path /pflege-und-funktionsdienst/details/?job=<uuid> vs /uebersicht-aller-stellen/details/?job=<uuid>; karriere.klinikum-nuernberg.de 7+1 old-host variants; jobs.malteser.de 4 old-slug duplicates; karriere-im.klinikverbund-allgaeu.de 12 on the old host; helios-gesundheit.de (www) 27 rows duplicate live P&I UUIDs and 5 duplicate Firecrawl rows; 20 Firecrawl rows (source 25, clinic NULL) duplicate adapter rows (11 share the adapter URL, 9 have a 40-hex URL). Mechanism: pflege_jobs/classify.py canonical_job_url folds only the shapes its rules know (wp_jobs host/slug changes are not among them); cli.cmd_link_cross merges same-source variants only through those rules and its cross-source pass takes only rows with clinic_id not null (cli.py l.299); the old URL still answers HTTP 200 so verify keeps it live; degraded boards never retire (TASK-191).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 canonical_job_url (or the link-cross pass) folds the shapes listed here, red-green from the real URL pairs, mutation-checked
- [ ] #2 Existing duplicate rows retired once as a reviewed change set (reason code duplicate, evidence = both URLs, the survivor is the row with the newest last_seen and a text); counts per board before and after
<!-- AC:END -->
