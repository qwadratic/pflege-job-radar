---
id: TASK-6
title: 'API audit follow-ups (2026-09-08 findings.md, consolidated)'
status: To Do
assignee: []
created_date: '2026-09-08 23:18'
updated_date: '2026-09-22 19:14'
labels:
  - api
dependencies: []
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Full findings: harness/evals/api-layer-audit-2026-09-08/findings.md. Untouched by this session -- API layer wasn't in scope of the 2026-09-22 crawler remediation. Consolidated from 5 separate tasks (was TASK-6/7/8/9/10) into one tracker, grouped by category below.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Cost-path correctness (was TASK-6, #6,7,17-19,21,28): spend_gate's adapter probe discards rows it already fetched then Firecrawl re-discovers them; GET /api/crawl/plan doesn't run the real per-clinic spend_gate/kill_switch simulation; CPP_BAR hardcoded 0.5 in web/pro.template.html vs real hunter.max_usd_per_posting (now $0.10); POST /api/clinics/{id}/refetch-career reuses jobs spend_gate (wrong verdict for a career-page run); kill_switch_status() not exposed via GET /api/firecrawl/credits; POST /api/hunter/start|run-once silently clears a real cost-stop reason with no ?force= guard; max_credits default/bounds differ between POST /api/crawl and GET /api/crawl/plan, neither rejects 0<max_credits<MIN_VIABLE_CAP
- [ ] #2 Docs/skill reference drift (was TASK-7, #8,9,22-24,33): docs/api.md, docs/auth.md, skill/references/api.md have wrong auth claims and are missing ~69 routes vs /api/openapi.json; list envelope claim only applies to some endpoints; field-level drift on 8+ endpoints. Regenerate from live responses + app/auth.py's actual prefix lists, rebuild web/skill/
- [ ] #3 Observability endpoints (was TASK-8, #10,11,20,34): GET /api/crawl/runs has no filter by status/mode/trigger/scope/clinic_id/time or search over run_log; no way to ask which clinics have never been crawled; no per-run cost-attribution endpoint; closed_postings has no list endpoint
- [ ] #4 Response-shape/field bugs (was TASK-9, #12,25,26,27,29,31,32): GET /api/clinics?has_jobs=0 is a no-op; size buckets hardcoded client-side duplicate taxonomy.json; run row/new counters renamed rows/new in coverage+billing vs n_rows/n_new elsewhere; POST /api/crawl vs GET /api/crawl/plan disagree on response key types; GET /api/schedules/presets exists but unused; next_run_at minute precision vs second precision elsewhere; autopilot stores Europe/Berlin timestamps compared lexically against UTC filters
- [ ] #5 Frontend over-fetching (was TASK-10, #14,15,30): #/clawl fires 12 requests with 3 literal duplicates; Firecrawl balance arrives via 3 endpoints at 3 refresh rates; clinicOptions() pulls the full ~400-row clinic table to build a 2-string dropdown; billToday() refetches the whole billing report on every render; boot chain is strictly sequential where Promise.all would do
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-22, re-verified against live code before acting (do not trust an 8-day-old audit snapshot): findings #1 (autopilot no gate), #2 (autocrawl/tick anonymous), #3 (public credit leak), #5 (kill switches unwired), #8/#9 (docs auth drift) are ALL already fixed by later work -- OWNER_READ/WRITE_PREFIXES cover autopilot/autocrawl/crawl/firecrawl/campaign, kill_switch() reads firecrawl.enabled and campaign.stopped, docs/api.md's header is accurate and self-generates via GET /api/agent/manifest. Only #7 (crawl/plan runs no real gate) was still genuinely open -- fixed this session: plan_for() now calls kill_switch() (cheap, no live fetch) and returns blocked/blocked_reason, wired through GET /api/crawl/plan and the pro.html plan-gate panel. spend_gate() itself deliberately NOT called per-clinic in the preview (it live-probes a routable clinic's adapter, too slow for every preview). Separately (Ivan, same session): /api/autopilot/* router unmounted and POST /api/autocrawl/tick disabled -- both confirmed zero real product usage (autopilot: one manual check 2026-09-11; tick: one rejected attempt ever, 2026-09-08) -- code untouched, re-enable by restoring the two commented-out lines in app/main.py. Remaining categories (#4 observability, #6 spend_gate discards probed rows, #10-18 medium) not re-verified -- treat as unconfirmed, not as open, until someone re-checks them the same way.
<!-- SECTION:NOTES:END -->
