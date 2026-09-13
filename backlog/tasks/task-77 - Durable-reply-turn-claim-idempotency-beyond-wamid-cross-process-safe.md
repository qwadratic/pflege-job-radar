---
id: TASK-77
title: 'Durable reply-turn claim (idempotency beyond wamid, cross-process safe)'
status: In Progress
assignee: []
created_date: '2026-09-13 11:14'
updated_date: '2026-09-13 11:25'
labels: []
dependencies: []
ordinal: 77000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Recon found our only dedup primitive is the wamid UNIQUE constraint plus an in-process RLock -- no durable, cross-process claim. That's fine today (webhook-only, single process), but the new catch-up driver (separate task) runs as a second process/entrypoint that can race the webhook for the same owed thread. Build a durable claim, closely modeled on the real system's candidate_reply_turn_claims: whichever path (webhook or catch-up) claims an inbound message's reply first proceeds; the other skips. Must not permanently block a legitimate retry (a claim left in a stuck/skipped state must be reclaimable).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New table: claim keyed on (phone, turn_key=inbound wamid), with a state column (in_progress/sent/skipped_rate_cap/skipped_no_send/skipped_error)
- [x] #2 claim_reply_turn(conn, phone, turn_key): True if this caller may proceed, False if another caller already holds an active or terminal ('sent') claim
- [x] #3 A stale in_progress claim (older than a defined timeout) or a non-terminal skipped state is reclaimable
- [x] #4 finish_reply_turn_claim(conn, phone, turn_key, state) records the outcome
- [x] #5 app/wa/api.py's webhook reply path and the new catch-up driver both call the same claim/finish functions -- no duplicated logic
- [x] #6 Unit tests cover: first claim succeeds, concurrent second claim on the same turn_key fails, a stale claim is reclaimable, a terminal 'sent' claim is never reclaimable
- [x] #7 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/store.py: wa_reply_turn_claims(phone, turn_key, state, claimed_at, updated_at), PK(phone,turn_key). claim_reply_turn() inserts optimistically, catches IntegrityError, then only blocks on a fresh in_progress row or a terminal 'sent' row -- any skipped_* state or a stale (>300s) in_progress row is reclaimed via UPDATE. finish_reply_turn_claim() records the outcome. Wired into app/wa/api.py's new process_owed_turn() (shared by _handle_one and, next, the catch-up driver).
<!-- SECTION:NOTES:END -->
