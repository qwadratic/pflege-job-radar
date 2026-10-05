---
id: TASK-402
title: >-
  apply_clinic_corrections cannot clear a careers_url: the ingest clinics upsert
  coalesces an empty string to the stored value
status: To Do
assignee: []
created_date: '2026-10-05 11:52'
labels:
  - tools
  - registry
dependencies: []
priority: low
ordinal: 198000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-10-05, clinic 66103: tools/apply_clinic_corrections.py --push with careers_url '' reported 'clinics upserted 1/1' and then failed its own read-back ('NO 66103 careers_url=<old>', 'NOT ALL APPLIED', 0 corrections rows), because the ingest function's clinics upsert does coalesce(nullif(excluded.careers_url,''), stored) for ats_type and careers_url (pflege_jobs/sinks.py write_clinics docstring). So a wrong careers_url can be replaced, never removed, through the tool. The change was applied once by a direct Postgres UPDATE with backup, read-back and a corrections row (job-dir script clear_careers_url_66103.py). Decide: let the tool clear a field on request (an explicit marker such as null meaning clear, handled by the edge function) or give it the direct-SQL path the posting tools use.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A clinic field that must be empty (careers_url, ats_type) can be cleared through tools/apply_clinic_corrections.py with backup, read-back and a corrections row
- [ ] #2 The tool refuses to report success for a change its read-back did not see (already so: keep it)
<!-- AC:END -->
