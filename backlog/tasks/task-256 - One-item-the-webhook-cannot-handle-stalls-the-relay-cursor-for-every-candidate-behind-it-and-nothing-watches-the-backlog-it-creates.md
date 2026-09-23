---
id: TASK-256
title: >-
  One item the webhook cannot handle stalls the relay cursor for every candidate
  behind it, and nothing watches the backlog it creates
status: In Progress
assignee: []
created_date: '2026-09-23 08:03'
updated_date: '2026-09-23 11:51'
labels:
  - rail-critique
  - operator-blind
dependencies: []
priority: medium
type: bug
project: whatsapp
ordinal: 203000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the phone-rail critique run, 2026-09-23, and independently verified against the code. Location: bridge/relay_pull.py:296. Severity: operator-blind. A comment in the code already states this limit -- the question is whether that trade is still acceptable now the rail carries live conversations. 

HOW IT HAPPENS: One item the webhook answers 200-with-no-results (a phone_number_id mismatch, a message parse_message declines, a 5xx from a bad pending row) reaches the head of the outbox. drain_once stops there; run() retries it forever with backoff to 60 s. Every message appended after it — from every other candidate — sits unacked on the mini behind it.

WHAT IT COSTS: A single poison item silences the whole rail for everyone. The only signal is a repeating journald line on the VPS; the mini's inbound_backlog grows with a rising oldest_unacked_at, and per the previous finding nothing reads that either.

PROPOSED DIRECTION (not a decision): Keep stopping rather than skipping. Make the stall visible without someone tailing journald: the same heartbeat consumer proposed above should treat a cursor that has not moved while the backlog is non-empty as the loudest thing on the rail. Having the relay report which inbound_id it is stuck on would let an operator settle it in one command.

VERIFICATION NOTES: CONFIRMED, and the finder is right that the stop-don't-skip behaviour is correct and should stay. deliver() raises RelayError on an empty results list (relay_pull.py:295-300); drain_once (:305-314) advances the cursor only after deliver() returns, so it stops at the head item; run() retries with backoff capped at 60 s forever. The trigger set is broader than the documented one: accept_payload appends to results only when parse_message returns a parsed message (api.py:521-529), so any message the server declines to parse — and any 5xx from the webhook, e.g. the orphan-pending RuntimeError in store.pending_inbound — produces the same stall. Deliberately admitted at relay_pull.py:19-23 ('A stalled cursor with a loud log is recoverable; a cursor that walked past a candidate's question is not'). It is therefore not a wrong design decision but the same missing consumer as the previous finding: the loud log goes to journald and nothing reads journald. Overlaps finding 9 and should be fixed by the same change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The finding is either fixed, or closed with a written argument for why it must not be fixed
- [ ] #2 A test fails without the fix and passes with it (or the closing argument explains why no test is possible)
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified the finding is real but narrower than described, then fixed the surviving gap.

Read relay_pull.py in full plus the cited paths (api.py:parse_message/_accept_change/_number_matches,
bridge_api.py:wa_bridge_webhook/receive_bridge_webhook, envelope.py:meta_envelope, store.py via
api.py:drain_pending/pending_inbound) and confirmed:

- The stall itself is real: deliver() raises RelayError on empty results; drain_once() only advances
  the cursor after deliver() returns, so it stops at the head item and retries forever with backoff
  capped at 60s. Already covered by a passing test
  (test_a_webhook_that_handled_nothing_stops_the_relay_rather_than_walking_past_it).
- Two of the three triggers the verification notes list do NOT apply to bridge-originated traffic:
  meta_envelope() (envelope.py:92-96) raises ValueError before send if sender/inbound_id are empty,
  and it only ever emits type in {text, image, video, document, audio}; parse_message (api.py:152-204)
  returns non-None unconditionally for those once wamid+phone are present, which meta_envelope already
  guarantees. So parse_message cannot decline for this rail's own envelopes. And store.pending_inbound's
  orphan RuntimeError is only reachable via drain_pending, which only runs inside the background
  ThreadPoolExecutor (api.py:_process_in_background), never in the synchronous request path
  (bridge_api.py:wa_bridge_webhook -> accept_payload + submit_accepted -> accepted_summary) -- it
  cannot produce a 5xx to relay_pull's POST. The one reachable trigger for the empty-results stall is a
  WA_BRIDGE_PHONE_NUMBER_ID mismatch via api._number_matches, a real operational event per TASK-146's
  REQUIRED_ENV comment.
- TASK-255's fix (Relay.check_watcher_alarm(), wired into run() on ALARM_CHECK_INTERVAL_SEC=60s,
  reading executor.health().inbound.oldest_unacked_at) is already in this working tree, uncommitted,
  and already covers "nothing watches the backlog it creates" -- a stuck cursor keeps oldest_unacked_at
  stale, and the alarm fires on it independent of the retry backoff. Did not touch
  check_watcher_alarm/ALARM_CHECK_INTERVAL_SEC/oldest_unacked_at logic; that is TASK-255's, already
  tested (38/38 passing before my change).

What was still missing, and is the actual gap this task closes: neither RelayError message in
deliver() named which item it stopped on, even though item['id'] and payload['inbound_id'] were
already in scope at both raise sites. An operator saw the alarm plus a repeating "handled no message"
line but had to separately query the mini's outbox to find the stuck candidate.

FIX: in bridge/relay_pull.py Relay.deliver(), both RelayError messages now name the item --
"bridge-webhook answered {status} for outbox #{item['id']} (inbound_id=...)" and "bridge-webhook
accepted outbox #{item['id']} (inbound_id=...) but handled no message ...". No other lines changed;
did not touch Cursor, drain_once, run(), the ledger, executor.py, or any send-path code.

TEST: extended test_a_webhook_that_handled_nothing_stops_the_relay_rather_than_walking_past_it in
tests/test_bridge_relay.py to assert "outbox #1" and the message's inbound_id appear in
str(caught.value). Verified it fails without the fix (git stash of relay_pull.py alone) and passes
with it. Ran only tests/test_bridge_relay.py: 38 passed. Did not run the full suite per house rule
(one full run happens in the owner's verification pass).

Left at In Progress; did not check acceptance criteria or mark Done, per instructions -- that happens
in the verification pass. Did not commit.
<!-- SECTION:NOTES:END -->
