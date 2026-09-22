---
id: TASK-149
title: Document the live phone rail and the handset operations in docs/whatsapp.md
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 13:23'
updated_date: '2026-09-21 13:25'
labels:
  - wa-transport
dependencies:
  - TASK-147
documentation:
  - docs/whatsapp.md
priority: high
type: docs
ordinal: 157000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The shipped Transports section described the bridge rail as a thing in the tree. Since 2026-09-21 it is a thing on a handset: the executor runs on the mini, a pull relay feeds inbound into our webhook, a thread is pinned to the rail, and a real candidate message was answered over it with a read-back delivery tick. A doc that lags a live rail is worse than no doc -- the last revision still told a reader that WA_TRANSPORT=bridge raises. The same section has to carry the new handset operations, because an operator reaching for a destructive command reads this file first, and because the PLANNED-versus-EXISTS discipline is the only thing separating what the rail does from what we intend it to do. Line citations in that section were checked against the tree; several were stale after a day of live edits.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The Transports section describes the rail as it EXISTS: executor on the mini, the ssh -L outbound leg, the pull relay inbound leg, the per-thread rail pin, and the tick-instead-of-provider-message-id invariant
- [x] #2 The first live exchange is recorded with its timestamps and with where its record actually lives, including that the harness-side rows were wiped by the nightly test wipe
- [x] #3 An operations subsection documents each operation, its guarantees, the destructive discipline and the exact CLI command for each
- [x] #4 Work that is not built is marked PLANNED rather than described as done
- [x] #5 Every file:line citation in the rewritten section resolves to what the text says it is, checked against the tree at the time of writing
- [x] #6 The env block no longer calls the bridge rail named-but-not-built, and names where the rail's own env file lives
- [x] #7 The nightly test-thread wipe records Ivan's 2026-09-21 re-confirmation that it clears everything
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Rewrite in place: (1) Transports lead and the second-rail paragraphs, (2) a three-legs paragraph and a live-evidence paragraph, (3) the rail-pin paragraph, (4) the HTTP route table plus the new routes, (5) a new subsection 'Handset operations as coded tools (TASK-147)', (6) the Operations env block, (7) the Test numbers retention bullet. Verify each citation with sed before writing it.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Rewritten sections in docs/whatsapp.md: (1) Transports lead -- two rails, the seam's seven call sites re-derived (api.py:321, :598, :977, :1045; luna/campaign.py:1111, :1155; luna/import_history.py:534), transport.build/rail_for/get_client named with their lines; (2) the per-phone seam paragraph re-pointed at api.py:553 and :587-598; (3) a new three-legs paragraph -- executor on the mini, the ssh -L outbound leg with WA_BRIDGE_URL=127.0.0.1:18793, the pull relay leg and its fetch-post-persist-ack order, and rail.env as the rail's own env file; (4) a new live-evidence paragraph; (5) a new rail-pin paragraph (store.py:194, pin_rail :323, rail_of :317, rail_counts :354, pinned at api.py:981 and :1009); (6) the rail table's bridge row now carries the first-exchange timestamp and TASK-147; (7) the route table gained /v1/chats, /v1/thread, the two destructive routes, the four broadcast routes and /v1/audit, plus the one-line statement of the loopback+token guard and the error envelope shape; (8) a new subsection 'Handset operations as coded tools (TASK-147)'; (9) the env block's WA_TRANSPORT lines and four WA_BRIDGE_* lines; (10) the Test numbers retention bullet.

Verification: the live exchange was read back from relay.sqlite (relay_delivered accepted, cursor 1, 11:35:05.377Z), the pflege-wa.service journal (bridge-webhook 200) and the mini ledger (two bubbles sent, tick Zugestellt); the deployed state from systemctl --user and GET /v1/health on the mini (service restarted 13:15:19 UTC, broadcast runner heartbeat present, /v1/chats 401 without a token); the caps quoted in the operations subsection from bridge/governor.py Pacing; the test count from running PFLEGE_TESTS_OFFLINE=1 pytest -q -m 'not network and not llm' tests/test_bridge_operations.py tests/test_wa_bridge_cli.py (75 passed). Every file:line in the section was resolved against the tree by script afterwards; twelve citations were stale after a day of live edits and were corrected (store.py:42 to :44, api.py:955-958 to :1030-1031, api.py:878 to :906, store.py:297-304 to :372-375, campaign.py:655-656 to :675-676, luna_brain.py:943 to :1020, campaign.py:379-396 to :384, campaign.py:115 to :120, api.py:929 to :979, api.py:962-966 to :1034-1038, config.py:168 to :201, luna_brain.py:988-991 to :1069-1077).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
docs/whatsapp.md now describes the phone rail as it exists -- executor on the mini, ssh -L outbound leg, pull relay inbound leg, immutable per-thread rail pin, tick instead of a provider message id -- with the first live exchange (2026-09-21 11:35 UTC) and where its record lives, a new subsection documenting the seven handset operations with their guarantees, the destructive discipline and the exact CLI command for each, and PLANNED kept for what is not built. Verified against the running services, the relay and mini ledgers, and the tree: every file:line citation in the section was resolved by script and twelve stale ones corrected.
<!-- SECTION:FINAL_SUMMARY:END -->
