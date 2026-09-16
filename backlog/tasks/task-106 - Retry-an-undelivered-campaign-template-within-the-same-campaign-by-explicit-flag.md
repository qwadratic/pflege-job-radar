---
id: TASK-106
title: >-
  Retry an undelivered campaign template within the same campaign by explicit
  flag
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 22:15'
updated_date: '2026-09-15 01:31'
labels: []
dependencies:
  - TASK-103
type: feature
ordinal: 106000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-103 plans a template that Meta later reported failed (status webhook, e.g. 131042 unsettled payments, 131026, 131049) as delivery_failed and never resends it under the same campaign id; a retry needs a new --campaign-id, which splits one campaign across ids and reports. Ivan 2026-09-14: allow the retry within the same campaign via an explicit flag. The live 131042 failure on 2026-09-14 is exactly this case: after billing is fixed the same campaign must be resendable.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 with an explicit flag (e.g. --retry-delivery-failed) the sender re-sends phones planned delivery_failed in the same campaign; without it behaviour is unchanged
- [x] #2 every attempt keeps its own wamid, error code and delivery status (history is not overwritten), --status shows all attempts, and a later reply is matched to the attempt it answers
- [x] #3 ownership, claim and card.campaign stay consistent across attempts; phones that declined, stopped or replied in the meantime are not resent
- [x] #4 offline tests cover failed then retried then delivered, failed twice, and reply-after-first-attempt; runbook updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. store.py: wa_campaign_sends becomes one row per attempt, pk (campaign_id, phone, attempt); every attempt keeps state, wamid, error/error_code/error_payload, prior owner, ownership_restore, claimed/sent/finished. ensure_campaign_schema(c): an existing TASK-103 table (column attempts, pk campaign_id+phone) is rebuilt in one BEGIN IMMEDIATE transaction (new table, rows copied with attempt = attempts, old dropped, renamed, index recreated); idempotent, re-checked under the lock; live_db and the read-only snapshot both call it.
2. store.py: campaign_send = the latest attempt; campaign_send_attempts = all attempts of one phone; campaign_sends = latest per phone; campaign_claims_after counts every attempt (each is a claim and a POST). claim_campaign_send inserts attempt n+1: failed as before, in_progress/uncertain only with retry_uncertain (a superseded in_progress attempt is finished as uncertain), sent only with retry_delivery_failed and the latest Meta status of its wamid failed; raises otherwise. finish/reconcile take the attempt number.
3. campaign.py: --retry-delivery-failed. decide(): without the flag delivery_failed as today (reason names the flag); with it: stopped, declined, marketing opt-out, or any inbound message since the campaign first claimed the phone (new skip_replied) skip; else retry_delivery_failed (SENDABLE, history source checked as for any send). Ownership: a retry keeps the owner recorded before the first flip (TASK-103 rule), a 4xx on the retry restores it. card.campaign = the latest sent attempt (record_campaign_send per sent attempt); the undelivered attempt's outbound row stays, hidden from Luna by its failed status.
4. --status: per phone all attempts (attempt, state, wamid, send error code, delivery status/errors, claimed/sent/finished, prior owner, restore, unlinked statuses for uncertain attempts) plus replies matched to the attempt they answer (context id = that attempt's wamid, else the latest attempt that may have reached them before the reply), totals incl. attempts and error codes across attempts. Report settings carry the flag.
5. Tests (offline): failed -> retried -> delivered (attempt rows, card.campaign, ownership, nudge claims, Luna payload), failed twice (plus a 4xx on a retry restoring the owner, then retry_failed), reply after the first attempt (skip_replied, reply matched to attempt 1), declined/stopped meanwhile not resent, migration of a TASK-103 table (idempotent, snapshot copy), existing tests adjusted to attempt rows.
6. Docs: docs/whatsapp.md campaign sender (plan actions, table, status, runbook for 131042 retry), docs/rollout-runbook.md step 7, module docstring. Full offline suite once.

Review fixes 2026-09-15:
R1: decide(): once any attempt of this campaign went out (state sent, also one Meta reported undelivered), every resend (retry_delivery_failed, retry_failed, retry_uncertain) plans skip_replied when the candidate wrote since the first claim. Plain TASK-103 retry_failed/retry_uncertain without an earlier sent attempt unchanged (open for Ivan). Test: undelivered -> flagged retry rejected 4xx -> inbound -> rerun skip_replied, no POST; same after an uncertain retry.
R2: reach_anchor(): a sent attempt whose latest Meta status is failed never reached the candidate, so it is no time anchor for replies or unread media. --status reports messages since the first claim that answer no attempt (not_answering_an_attempt). Test updated.
R4 (runbook): docs/rollout-runbook.md step 7 defines H and appends it to every non-status command.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Store: wa_campaign_sends is one row per attempt (pk campaign_id+phone+attempt); ensure_campaign_schema rebuilds a TASK-103 table in one BEGIN IMMEDIATE transaction (attempt = attempts); claim/finish/reconcile are per attempt; a superseded in_progress attempt is finished as uncertain. Sender: Retry(uncertain, delivery_failed), --retry-delivery-failed, new actions retry_delivery_failed and skip_replied, --status per attempt with replies matched to attempts. Existing sender tests adjusted (45 passed); new tests next.

Tests added (tests/test_wa_campaign_sender.py): failed 131042 -> --retry-delivery-failed -> delivered; failed twice (131042, 131049) plus a 131026-rejected retry restoring the pre-campaign owner and retry_failed attempt 4; reply after the first attempt (skip_replied, reply matched to attempt 1); stopped/declined/131050 after an undelivered attempt; crashed attempt superseded; store claim rules per attempt; TASK-103 table rebuild (read-only status writes nothing, send continues at attempt 3). Mutation checks via a throwaway plugin (deleted): skip_replied disabled -> 1 test fails; reply matching always 'time' -> 3 fail; flag ignored -> 4 fail. Read-only --status against a copy of data/wa.sqlite: 0 phones, exit 0, file hash unchanged. Docs: docs/whatsapp.md (plan actions, new 'Attempts and retry of an undelivered template (TASK-106)', status, runbook), docs/rollout-runbook.md step 7.

Full offline suite (single process): 1516 passed, 126 skipped, 61 deselected in 140 s (1510 before + 6 new tests). Open for Ivan: skip_replied applies only to --retry-delivery-failed; --retry-uncertain and retry_failed still resend to a phone that wrote since (TASK-103 behaviour kept, AC1 'unchanged without the flag').

Review fixes 2026-09-15 (not committed):
- R1 campaign.decide(): once any attempt of the campaign went out (state sent, delivered or not), every resend (retry_delivery_failed, retry_failed, retry_uncertain) plans skip_replied when the candidate wrote since the first claim; reason names the latest attempt and the sent one ('attempt 2 failed after attempt 1 sent ... wamid ...; the candidate wrote 1 message(s) ...: not resent'). The uncertain gate without --retry-uncertain still comes first (action uncertain, exit 1). New test test_a_candidate_who_wrote_after_a_rejected_or_uncertain_retry_is_not_resent: 131042 undelivered -> flagged retry HTTP 400 (failed) / network error (uncertain) -> inbound, Luna answered -> send without flags (skip_replied / uncertain), dry-run and --send with both flags: skip_replied for both, no POST, 2 attempts each.
- R2 reach_anchor(): a sent attempt whose latest status is failed is no time anchor (no replies matched to it, no unread media from it). status_rows: not_answering_an_attempt {count, first_at, last_at} = inbound since the first claim matched to no attempt; printed; totals.wrote_not_answering_an_attempt. test_a_candidate_who_wrote_after_the_first_attempt... now expects matched [] and not_answering_an_attempt count 1, totals replied 0.
- Mutation checks in an isolated copy under .tmp (deleted): went_out limited to the undelivered branch -> the new test fails; failed-delivery anchor exclusion removed -> the updated test fails.
- Docs: module docstring (ATTEMPTS, --status), docs/whatsapp.md (plan actions skip_replied, Attempts, Status, tests), docs/rollout-runbook.md step 7 (R4: H defined, $H on probe/dry-run/send/retry commands; skip_replied scope).
- Full offline suite (single process): 1571 passed, 126 skipped, 66 deselected in 141 s.
- Still open for Ivan: retry_failed/retry_uncertain with no attempt out yet (first attempt rejected or uncertain) do not check inbound messages (TASK-103 behaviour).

Final verification 2026-09-15 (~01:20 UTC, after review + adversarial verify + fixer): offline suite 1571 passed, 126 skipped, 0 failed. Live llm, one at a time: campaign full funnel 3/3 non-empty shortlist, flexible funnel 1/1 (5 clinics), imported opt-out silence then re-engagement 1/1, voice-note reply from transcript 1/1 (fake STT), misheard town asked back 1/1. Follow-up fix 01:30 UTC: the unmatched-department ToolError no longer carries candidate-facing English (a run had copied it into a bubble and broken JSON); tests/test_wa_luna_tools.py 15 passed, Urologie funnel llm 2/2. Not yet deployed: pflege-wa.service restart pending Ivan's go.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
--retry-delivery-failed re-sends campaign templates Meta reported undelivered within the same campaign; every attempt keeps its own wamid, error and status, --status lists attempts and matches replies to delivered attempts, ownership stays consistent, and phones that declined, stopped or wrote since are not resent. Verified by offline tests (131042 then delivered, failed twice, reply after first attempt) and the full offline suite.
<!-- SECTION:FINAL_SUMMARY:END -->
