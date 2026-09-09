---
id: TASK-8
title: 'API audit: observability endpoints for runs/coverage/billing (#10,11,20,34)'
status: To Do
assignee: []
created_date: '2026-09-08 23:18'
labels:
  - api
dependencies: []
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Full findings: harness/evals/api-layer-audit-2026-09-08/findings.md. (10) GET /api/crawl/runs has no filter by status/mode/trigger/scope/clinic_id/time or search over run_log -- the campaign routine reads journalctl on the VM because the API can't answer 'which runs hit a spend-gate refusal today'. (11) No way to ask which clinics have never been crawled (last_run_per_clinic only scans the newest 500 runs and conflates never-crawled with last-run-failed). (20) No per-run cost-attribution endpoint (which clinic/gate-verdict cost what) -- hunter regexes its own log today. (34) closed_postings has no list endpoint, only one-by-one by posting_id -- stripe_error values are invisible without opening sqlite directly.
<!-- SECTION:DESCRIPTION:END -->
