---
id: TASK-395
title: 'Pro WA read API (topology B): token-gated harness endpoints the board proxies'
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
labels:
  - pro-api
dependencies:
  - TASK-283.1
priority: high
type: feature
project: whatsapp
ordinal: 270000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's decisions, 2026-09-29:
- the harness serves a read-only, bearer-token /api/wa/*;
- the board (161.210.92.90) proxies it server-side with WA_API_BASE / WA_API_TOKEN;
- the owner gate applies at the board; Valentyn gets the Pro passphrase;
- phone_masked keeps at least the last 4 digits;
- phase 1 is read-only.

The contract is docs/wa-dashboard.md on qwadratic feat/pro-leads-view (draft PR #2, pflege-fe session). The Pro Leads view is already built against it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 GET /api/wa/threads is paged. Envelope: {total, limit, offset, next_offset, test_threads, generated_at, source, rows}. No cap; next_offset is always present and null on the last page; source is "harness@<host>"
- [ ] #2 Row fields follow the contract: phone_masked keeps the last 4 digits; the raw phone never appears in any response or URL; no cv_text; thread_id is opaque
- [ ] #3 GET /api/wa/threads/{id}, /threads/{id}/messages (before_id/after_id cursor; deleted rows have body null; no wamid) and /api/wa/health follow the contract
- [ ] #4 The harness requires a bearer token. The board proxies with WA_API_BASE/WA_API_TOKEN, is owner-gated, answers 503 when WA_API_BASE is unset and never reads a local DB. Error codes 401/403/404/502-504 follow the contract
- [ ] #5 Deny tests cover anonymous and customer sessions on the board
- [ ] #6 A fixtures JSON (6-8 synthetic threads) and the Pydantic models are handed to pflege-fe
- [ ] #7 Nginx location on tasker-dispatcher-01 (Ivan runs the sudo step); base URL handed to pflege-fe
<!-- AC:END -->
