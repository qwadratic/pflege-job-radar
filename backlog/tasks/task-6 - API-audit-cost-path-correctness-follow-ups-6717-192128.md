---
id: TASK-6
title: 'API audit: cost-path correctness follow-ups (#6,7,17-19,21,28)'
status: To Do
assignee: []
created_date: '2026-09-08 23:18'
labels:
  - api
dependencies: []
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Full findings: harness/evals/api-layer-audit-2026-09-08/findings.md. Remaining cost/spend-gate items not fixed in commit 29a3136: (6) spend_gate's adapter probe discards rows it already fetched, then Firecrawl re-discovers them -- have it return rows and skip the paid call when it already has them; (7) GET /api/crawl/plan still doesn't run the real per-clinic spend_gate/kill_switch simulation (only got a placeholder need for kill_switch_status -- item 19); (17) CPP_BAR hardcoded 0.5 in web/pro.template.html vs the real hunter.max_usd_per_posting (now $0.10); (18) POST /api/clinics/{id}/refetch-career reuses the jobs spend_gate, wrong verdict for a career-page run; (19) expose kill_switch_status() via GET /api/firecrawl/credits; (21) POST /api/hunter/start|run-once silently clear a real cost-stop reason with no ?force= guard, and Hunter.dry_run() has no HTTP route; (28) max_credits default/bounds differ between POST /api/crawl and GET /api/crawl/plan, and neither rejects 0<max_credits<MIN_VIABLE_CAP at the boundary.
<!-- SECTION:DESCRIPTION:END -->
