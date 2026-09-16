---
id: TASK-93
title: Durable cross-process dedup claim for outbound nudges/campaigns
status: Done
assignee: []
created_date: '2026-09-13 20:30'
updated_date: '2026-09-13 20:34'
labels: []
dependencies: []
type: feature
ordinal: 93000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked to add the reference system's 'wake-fingerprint dedup' (explicitly deferred in TASK-92) because more template-driven outbound campaigns are planned, each a genuinely separate trigger path that could independently decide to message the same candidate at nearly the same moment -- app/wa/luna/followups.py's own wa_followups_sent table has no unique constraint (a real streak-scoped race could still double-insert), and ST._lock only serializes within one process, not across the separate oneshot processes multiple campaign types imply. Same shape as TASK-77's wa_reply_turn_claims/claim_reply_turn (already proven, durable, cross-process, atomic INSERT-based claim) but simpler: a nudge is never 'owed' the way an inbound reply is, so nothing here needs to be reclaimable -- a claim that never results in a send is just a nudge that did not go out that round, not a lost message.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/wa/store.py gains wa_nudge_claims(phone, fingerprint, claimed_at) with primary key (phone, fingerprint), and claim_nudge(conn, phone, fingerprint) -> bool using the same atomic INSERT/IntegrityError pattern as claim_reply_turn
- [x] #2 app/wa/luna/followups.py calls claim_nudge before send_and_record, with a fingerprint that includes the current streak anchor (last_inbound_at) so a later, legitimate streak at the same tier index is never falsely blocked
- [x] #3 a second concurrent claim on the same (phone, fingerprint) is rejected and does not send a duplicate nudge
- [x] #4 the primitive is generically named/placed (not followups-specific) so a future template-driven campaign can reuse it directly
- [x] #5 offline tests cover claim_nudge directly (store-level) and the followups.py integration (two overlapping runs, same streak, do not double-send)
- [x] #6 docs/whatsapp.md's stale pre-TASK-85 'Deliberately missing' bullet (still claiming no follow-up cadence/quiet-hours/dedupe tables) is corrected
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented app/wa/store.py:wa_nudge_claims(phone, fingerprint, claimed_at) + claim_nudge(conn, phone, fingerprint) -> bool, same atomic INSERT/IntegrityError pattern as TASK-77's claim_reply_turn but deliberately non-reclaimable (a nudge is never 'owed' the way a reply is). Wired into app/wa/luna/followups.py:run() -- fingerprint is f'followup:{tier}:{since}' where since is the same streak-anchor value _eligible_tier already uses, so a later legitimate streak reusing the same tier index is never falsely blocked by an earlier claim. Generic table/function naming and placement (app/wa/store.py, not followups.py) so a future template-driven campaign can call ST.claim_nudge directly with its own fingerprint scheme. Tests: tests/test_wa_store_claims.py gets 3 new store-level tests (first claim succeeds, second on the same phone+fingerprint fails, different fingerprint/phone/streak-anchor do not interfere); tests/test_wa_luna_followups.py gets 1 integration test simulating two overlapping processes (two separate ST.db() connections) racing on the same eligible thread -- only one claim wins. Updated followups.py's own docstrings (module + run()) and docs/whatsapp.md: fixed a pre-existing stale 'Deliberately missing' bullet from before TASK-85/92/93 landed (it still claimed no follow-up cadence/quiet-hours/dedupe tables existed at all) and added a TASK-92/93 addendum to the TASK-85 paragraph. Offline suite: 1168 passed (up from 1164). No service restart needed -- pflege-wa-followups.service is Type=oneshot, next timer tick runs the new code from disk.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a durable, cross-process dedup claim (app/wa/store.py:wa_nudge_claims/claim_nudge) modeled directly on TASK-77's proven claim_reply_turn pattern, wired into followups.py's send path so two independent trigger paths deciding to nudge the same candidate at nearly the same moment cannot both send -- the gap Ivan flagged ahead of adding more template-driven campaigns, each a genuinely separate process ST._lock's in-process serialization cannot protect against. Generic enough for those future campaigns to reuse directly. Also corrected stale pre-TASK-85 documentation in docs/whatsapp.md that still claimed no follow-up cadence, quiet-hours, or dedupe tables existed. Verified: 4 new tests (3 store-level, 1 simulating two racing processes), full offline suite green at 1168 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
