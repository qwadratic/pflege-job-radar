---
id: TASK-262
title: >-
  One item the webhook will not handle wedges all inbound behind it, and the
  only signal is a log line every 60 s
status: To Do
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-26 08:49'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 209000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/relay_pull.py:305. Severity: operator-blind. A comment in the code already states this limit -- the question is whether that trade is still acceptable now the rail carries live conversations. 

HOW IT HAPPENS: rail.env's WA_BRIDGE_PHONE_NUMBER_ID drifts from the server's. api._number_matches skips the message, the webhook answers 200 with no results, the relay refuses to advance (correctly) and retries every 60 s. Every later candidate reply queues behind that one item while the executor, the tunnel and the relay unit all report active.

WHAT IT COSTS: Total inbound silence on a rail whose every other surface looks healthy; candidates' answers accumulate unread on the mini until someone reads journalctl for the right unit.

PROPOSED DIRECTION (not a decision): The stop-and-shout behaviour is deliberate and right; what is missing is that 'loud' means journald only. Make the relay's own state readable from the VPS side (cursor position, last successful delivery time, current error) so /api/wa/health can carry 'inbound last moved at T', and give a genuinely unhandleable item a way out of the head of the line -- a parked list that keeps the item and its reason while the rest drains -- so one bad envelope delays one candidate rather than all of them.

VERIFICATION NOTES: drain_once iterates fetch() and calls deliver() per item; deliver raises RelayError on an empty results list, which propagates out of the loop, so the pass stops at the first refusal and the cursor never advances past it (relay_pull.py:305-314, 283-301). run() catches, logs, closes the tunnel and doubles the backoff to a 60 s ceiling forever (relay_pull.py:316-334). The cursor is one integer (Cursor.position, relay_pull.py:105-107), so everything later queues behind the bad item while the mini keeps appending. The documented trigger is real -- WA_BRIDGE_PHONE_NUMBER_ID mismatch, named in REQUIRED_ENV (relay_pull.py:79) and in deliver's own error text. Nothing on the VPS reports relay lag: /api/wa/bridge-health only proxies the mini's /v1/health, which nobody polls, and cmd_health does not print inbound backlog. No message is lost -- unacked inbound rows are never swept (ledger.py:1085-1087) -- so the impact is stall, not loss.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26: folded into TASK-315 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
