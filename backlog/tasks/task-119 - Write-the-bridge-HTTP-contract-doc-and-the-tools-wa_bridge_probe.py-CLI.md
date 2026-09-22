---
id: TASK-119
title: >-
  Finish the bridge HTTP contract in docs/whatsapp.md: literal bodies for every
  route as built, and the error-code table
status: To Do
assignee: []
created_date: '2026-09-21 01:21'
updated_date: '2026-09-21 13:24'
labels:
  - wa-transport
dependencies:
  - TASK-147
  - TASK-148
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: docs
ordinal: 127000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rescoped 2026-09-21, after the rail went live. Two thirds of this task were overtaken by events and by other tasks, and what it still asks for was written against a contract that no longer exists.

Superseded: the probe CLI (tools/wa_bridge_probe.py) -- tools/wa_bridge.py (TASK-148) drives the real executor over the existing ssh -L and covers every route that exists; a probe for /v1/batches and /v1/media would exercise two routes that were never built. The 202-first framing -- there is no 202 on this rail: 200 is terminal and a paced request is refused 429 rail_parked with a next_slot_at (TASK-146). The rail, pin, id-space, kill-switch and tick-not-id prose -- written and verified in the Transports section (TASK-139, TASK-146, TASK-149).

What is genuinely still missing is the reference half: a literal request and response body for each route as it is actually built (including the operations routes from TASK-147), and the error-code table -- code, HTTP status, the exception raised on our side, and how app/wa/luna/campaign.py classifies it -- with the asymmetry that matters spelled out: a send failure with no status is uncertain, while import_history treats a None status as no answer about this media and aborts the run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 docs/whatsapp.md carries a literal request and response body for every route the executor actually serves, including the chat, thread, broadcast and audit routes, with 200 as terminal and 429 rail_parked as the paced refusal
- [ ] #2 The error-code table is present: code, HTTP status, retryable, the exception raised on our side, and how campaign.py classifies each one
- [ ] #3 The 424-for-send versus None-for-media asymmetry is stated with the two call sites that depend on it
- [ ] #4 Every route and code in the table was read off bridge/server.py and bridge/errors.py at the time of writing, not from an older plan
- [ ] #5 Docs and code comments are in English
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-21 rescope: ACs 1, 7 dropped (a contract that no longer exists, and a probe superseded by tools/wa_bridge.py, TASK-148). ACs 2, 3, 4, 6 are already satisfied in the Transports section by TASK-139, TASK-146 and TASK-149 and were folded out of this task. AC 5 (error-code table) survives and is now the core of it.
<!-- SECTION:NOTES:END -->
