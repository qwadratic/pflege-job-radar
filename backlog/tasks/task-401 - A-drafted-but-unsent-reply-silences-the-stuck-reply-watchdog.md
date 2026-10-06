---
id: TASK-401
title: A drafted-but-unsent reply silences the stuck-reply watchdog
status: To Do
assignee: []
created_date: '2026-10-01 16:14'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 277000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-01 while generating truthful Pro API fixtures (tools/wa_pro_fixtures.py, scenario 2222). The AUTOSEND-off / scope-refusal branch in app/wa/api.py writes an outbound row with kind=draft that was never delivered, and last_outbound_at is not updated. But luna/reporting.py:ball_for() looks only at the last message's direction, so the draft flips ball from us to them. pro_api._is_stuck short-circuits on ball != us, so stuck_reply clears although nothing reached the candidate and the escalation is still open. The same ball value also feeds the needs-a-human list.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 ball_for ignores outbound rows that were never delivered (draft, failed, refused) when deciding whose turn it is
- [ ] #2 A regression test: inbound, then a draft, still counts as ball=us and keeps stuck_reply
- [ ] #3 Fixtures regenerated (python -m tools.wa_pro_fixtures --write)
<!-- AC:END -->
