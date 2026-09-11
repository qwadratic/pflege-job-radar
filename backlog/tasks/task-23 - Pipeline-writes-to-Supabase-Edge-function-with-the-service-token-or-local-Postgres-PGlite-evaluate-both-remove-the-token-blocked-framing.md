---
id: TASK-23
title: >-
  Pipeline writes to Supabase: Edge function with the service token, or local
  Postgres / PGlite -- evaluate both, remove the token-blocked framing
status: To Do
assignee: []
created_date: '2026-09-09 12:21'
labels:
  - harvester
dependencies: []
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Several design steps were marked as blocked on a missing Supabase access token (new tables loads, incidents, harvest_report, site_status, columns verdicts and load_id on posting_observations, operator entity). Ivan (2026-09-09): nothing is blocked by that token. The Edge runtime already holds the service-role key in its environment, so any DDL or privileged write is a matter of writing the operation in the existing Edge function (edge/pflege-ingest/index.template.ts already exposes ops for inserts and upserts). Second vector he wants considered in the same evaluation: full migration of the pipeline state to a local Postgres, possibly PGlite (embedded Postgres in-process), which would also replace the SQLite state in app/runs.py that exists only because the app had no write credential on 2026-09-06. Compare both on: what changes for the live app on Supabase, what the ingest path looks like, how research mode reads, backup and portability of snapshots plus state, and effort.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A written comparison of Edge-function writes versus local Postgres/PGlite for the pipeline tables, with a recommendation and the migration steps for the chosen path
- [ ] #2 No task or document in the backlog describes a step as blocked on a Supabase token
<!-- AC:END -->
