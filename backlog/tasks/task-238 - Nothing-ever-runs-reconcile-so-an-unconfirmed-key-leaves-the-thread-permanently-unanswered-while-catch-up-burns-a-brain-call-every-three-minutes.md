---
id: TASK-238
title: >-
  Nothing ever runs reconcile, so an unconfirmed key leaves the thread
  permanently unanswered while catch-up burns a brain call every three minutes
status: To Do
assignee: []
created_date: '2026-09-23 08:02'
labels:
  - rail-critique
  - loses-messages
dependencies: []
priority: high
type: bug
project: whatsapp
ordinal: 185000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/executor.py:430. Severity: loses-messages. A comment in the code already states this limit -- the question is whether that trade is still acceptable now the rail carries live conversations. 

HOW IT HAPPENS: A send 504s (driver "unverified", or no tick inside 30 s). The key is UNCONFIRMED. Catch-up re-drives the owed turn every 3 minutes: it claims the turn, spends a real `claude -p` run at effort max, gets bubbles, and dies at the same 504 -- until the per-phone Luna cap trips, then again next hour, indefinitely, because the candidate is silent (they are waiting for the answer that never arrived).

WHAT IT COSTS: A candidate mid-conversation stops getting replies with no alert anywhere but an outbound state count in GET /v1/health that nobody watches, and the thread spends its whole hourly Luna budget producing nothing. If the model's action slug drifts on one of those passes (finding 1), the stuck turn resolves instead by delivering the whole reply twice.

PROPOSED DIRECTION (not a decision): The mini's hourly maintenance loop already runs and ledger.unresolved() (ledger.py:487) already returns exactly the rows that need answering -- a scan per unresolved key, on the phone that process already owns, whose only durable effect is a verdict the state machine already defines. Fix finding 4 first, or an auto-reconcile will confirm sends off older identical bubbles at machine speed. Failing that: the unresolved count and the age of the oldest unresolved row need to reach something that shouts, and catch-up should stop re-calling the brain for a turn whose key the bridge has already declared unconfirmable.

VERIFICATION NOTES: CONFIRMED, and the admission is real. Grepped the whole tree: the only non-test caller of reconcile is tools/wa_bridge.py:685 (cmd_reconcile). deploy/ holds catchup, followups, purge-test, known-phones, pflege-wa, pflege-web and pflege-hunter units and nothing else; server.py::maintenance_once (server.py:395-406) does the ledger sweep and retention.review_and_sweep only. _escalate (executor.py:417) marks the key UNCONFIRMED, which is not in RESENDABLE (ledger.py:52), so classify returns "replay" and _replay_response (executor.py:844) raises send_unconfirmed forever. tests/test_wa_bridge_rail_end_to_end.py:155 admits it in as many words ("nothing reconciles it yet"). ONE CORRECTION to the finder: LUNA_MAX_CALLS_PER_HOUR (=20, config.py:180) is a PER-PHONE cap -- api.py:882 calls ST.count_recent_luna_calls(c, t["phone"]) -- so the stuck thread burns its own hourly budget, not a global one; other threads are unaffected. At a 3-minute catch-up cadence that is exactly 20 calls per hour, i.e. the whole per-phone budget, spent producing nothing. The admission was made when the rail carried no live conversations; it now carries them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->
