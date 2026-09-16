---
id: TASK-103
title: >-
  Campaign sender: send an approved template to a phone list ourselves, dry-run
  first, no double sends, replies owned by Luna
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 14:24'
updated_date: '2026-09-16 14:13'
labels: []
dependencies:
  - TASK-98
  - TASK-99
  - TASK-100
  - TASK-101
  - TASK-102
type: feature
ordinal: 103000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-14: we (not the old system) send a reopen template ("new nursing jobs in Bayern") to old candidates; a colleague provides the template ID and the lead list; consent exists; the old system stays paused; managers know. No sender exists in this repo. Use /opt/clinic-dispatcher/apps/connectors/candidate_job_wohnung_outreach.py as inspiration. Pacing decided by Ivan: batches of about 10 every 10-15 minutes, only 09:00-21:00 Europe/Berlin. Non-responders get nothing further.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 a CLI takes a template ID, a lead list (phones, optional per-lead template variables) and a campaign id; dry-run is the default and prints the resolved template, rendered text and per-phone plan (owner, existing thread, stopped, prior sends, history import preview) without writing anything
- [x] #2 with --send each phone is claimed durably once per campaign; ownership moves to us before the send and returns to the prior owner if Meta rejects synchronously; the send is recorded as an outbound template row with the rendered text, the thread is created and seeded with campaign context and imported history
- [x] #3 per-phone results (wamid, error code/payload, later delivery status from status webhooks) are stored and reported; re-running skips sent phones and retries only failed ones; uncertain states are reported, never guessed
- [x] #4 sending follows the approved pacing and quiet window, stopped/opted-out phones are never sent, and a run can be resumed after interruption
- [x] #5 offline tests cover dry-run, idempotency, flip and revert, crash between flip and send, pacing and quiet window, status tracking, and a reply to the campaign reaching Luna with campaign context; docs/whatsapp.md has a campaign runbook
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. store.py (append): CAMPAIGN_SCHEMA wa_campaign_sends (campaign_id+phone pk, state in_progress|sent|failed|uncertain, attempts, template id/name/language, variables json, rendered text, prior_owner/reason/since, wamid, error/error_code/error_payload, claimed_at, sent_at, finished_at); non-committing claim/sent/failed/uncertain helpers; record_campaign_send gains commit=False + template_id/variables in row meta (card contract unchanged); save_thread split into an uncommitted update.
2. routing.py: third explicit write path flip_to_us_for_campaign(conn, phone, campaign_id) -> prior record, and restore_after_campaign_failure (only when the row is still the campaign flip); both without commit, so the campaign transaction commits claim + flip together.
3. app/wa/luna/campaign.py CLI: lead file CSV/JSON (phone + dotted variable keys body.1/header.1/buttons.0.payload, optional campaign-wide --params), canonicalize (invalid/duplicate/conflicting duplicate reported), resolve template by id (require APPROVED), validate+render per lead, plan per phone from wa.sqlite opened mode=ro (owner from wa_ownership or known-phones file, thread stage/ball, stopped, declined, marketing opt-out from user_preferences/131050, prior sends of this and other campaigns), history import preview (import_history.import_phone apply=False). Dry-run writes no DB row and posts nothing; report to stdout + JSON (default under ~/pflege-campaign-reports, 0600).
4. --send: WA_AUTOSEND required; per phone: import apply (optional) -> one BEGIN IMMEDIATE transaction: re-check eligibility, no claim in flight, reply-turn claim campaign:<id> (keeps the webhook worker off the card), wa_campaign_sends in_progress (attempt n) + ST nudge claim campaign:<id>:<n>, flip ownership -> POST template (definition+params) -> success: record_campaign_send + claim sent (one commit); Meta 4xx: claim failed with code/payload, wa_send_failures, ownership restored; network error/5xx/no wamid/crash: uncertain (in_progress after a crash), never resent without --retry-uncertain.
5. Pacing: at most batch_size claims per batch_interval (sliding window over claimed_at in the DB, so resumable across restarts), window 09-21 Europe/Berlin; outside: wait for the next start (logged) or exit 3 with --no-wait. Injectable clock/sleep for tests.
6. --status: per phone state, wamid, latest delivery status/errors (wa_message_statuses), replies since the send (count, first/last, button taps, reply_to campaign), declined/stage, owner; totals.
7. Verify follow-ups skip campaign non-responders; fix if a thread with earlier inbound would be nudged after the template.
8. Tests tests/test_wa_campaign_sender.py (tmp SQLite, fake Meta, frozen clock): dry-run writes nothing, claim idempotency, flip+restore, crash -> uncertain, pacing/window, stopped/declined/opted-out skipped, status tracking, e2e send -> router reply -> Luna payload with card.campaign.
9. docs/whatsapp.md campaign section + runbook (dry-run, send, status, stop, managers/old system), rollout-runbook step 7 deploy notes.

Fixer round (review 2026-09-14): 1. send_one re-checks the window right before the claim (after the history import) and defers to the window start; progress line prints the claim/send time. 2. _in_flight_until ignores stale claims. 3. delivery_failed plan action: a sent claim whose latest delivery status is failed is reported, not resent; retry under a new campaign id. 4. --mark-sent PHONE=WAMID reconciles an uncertain/in_progress claim from a stored status (recipient, since the claim, no stored message, category = template category); --status shows categories. 5. old-system Stopp in the history import -> skip_opted_out.

Repair round 1 (final verifier 2026-09-14): exit code -- a --no-wait run stopped before the list was done exits 3 even when the plan holds problem leads (they masked 3 as 1 on every run, e.g. after any delivery_failed); 1 stays for this run's send problems and for a finished run/dry-run with problem leads; test + docs. .gitignore data/known-real-system-phones.txt (hourly export of real-system phones, untracked, PII).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
REAL CAMPAIGN TEMPLATE (approved 2026-09-14, read-only lookup on WABA <WABA id>): id 1791710088522158, name recruitment_bayern_stellen_interesse_de, language de, MARKETING, parameter_format POSITIONAL. HEADER text "Neue Stellen in Bayern für Pflegekräfte"; BODY "Hallo, {{1}}. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir viele neue Stellen in Bayern. Haben Sie noch Interesse?" ({{1}} = candidate name); QUICK_REPLY buttons "Ja, ich habe Interesse" and "Nein, kein Interesse" (a tap arrives as type=button with that text/payload). The No button is a decline (TASK-101: fixed ack once, then silence); the Yes button is interest in Bayern (TASK-100). Test sends at 15:46 UTC to two test numbers (old-system test candidate id 14 and Ivan) were accepted by Meta; they were not recorded in wa.sqlite. Phone number verified_name is "Valentyn NDT".

Found live 2026-09-14 15:57 UTC: a test send of recruitment_bayern_stellen_interesse_de to Ivan was accepted by Meta (wamid returned) but the status webhook said failed, code 131042 "Business eligibility payment issue: your WhatsApp Business account has unsettled payments" (WABA <WABA id>, business <business portfolio id>). Paid template sends fail asynchronously this way while free-form replies inside the 24h window still work. Runbook: before a batch, send the template to an operator number and wait for a delivered status (TASK-99 status storage) -- an accepted POST alone proves nothing; --status must show per-phone failed codes like 131042.

2026-09-14: Implemented app/wa/luna/campaign.py (dry-run/--send/--status CLI), store.py campaign section (CAMPAIGN_SCHEMA wa_campaign_sends, claim/finish helpers without commit, message_statuses_for; record_campaign_send gains template_id/variables in row meta + commit=False; save_thread split into _update_thread), routing.py third explicit write path (flip_to_us_for_campaign / restore_after_campaign_failure, no commit). Verified gap and fixed: followups nudged a campaign non-responder who had written BEFORE the template (has_inbound true, ball them) -> now skipped when card.campaign.sent_at > last_inbound_at. tests/test_wa_campaign_sender.py 31 passed.

2026-09-14 decisions: (1) history import --apply runs right BEFORE the claim transaction, not after the flip: import_history._merge_card refuses while any claim of the phone is in flight, and the campaign holds reply-turn claim campaign:<id> from claim to record; the claim transaction re-checks stopped/declined/opt-out/in-flight, so a Stopp during the import is honoured (tested). (2) Outcome classes: wamid -> sent; HTTP 4xx (and TemplateParamsError) -> failed + ownership restored (no prior record -> row deleted; ownership_restore=changed if another explicit write happened); network error, timeout, HTTP 5xx, 2xx without wamid -> uncertain, ownership stays us; crash/Ctrl-C during POST -> in_progress, reported uncertain; never resent without --retry-uncertain, a retry keeps the owner recorded before the first flip. (3) Pacing is a sliding window over wa_campaign_sends.claimed_at (<= batch_size claims per interval, per campaign id), so restarts keep the pace; window/interval wait or --no-wait exit 3. (4) Delivery status is not copied into wa_campaign_sends; --status joins wa_message_statuses by wamid. (5) Opt-out = latest Meta marketing signal is stop (user_preferences marketing_messages stop w/o later resume, or failed status 131050); declined = card.declined; stopped = wa_threads.stopped. already_placed / qualification_ok=false / prior_placement.placed are shown, not skipped. (6) wa_campaign_sends lives in store.CAMPAIGN_SCHEMA, created only by the sender's connection; dry-run and --status read an in-memory copy of wa.sqlite opened mode=ro (SQLite may create empty -wal/-shm lock files when nobody has the db open). (7) --send requires WA_AUTOSEND=1 (docs invariant: without it nothing reaches Meta). (8) one campaign id = one template id (another raises). (9) report JSON default ~/pflege-campaign-reports (0700/0600). Tests: tests/test_wa_campaign_sender.py 32 passed; mutation checks via a throwaway plugin (no restore, 5xx as failed, no pacing, no window, no opt-out skip, dry-run on live db, uncertain resent, no in-transaction recheck, followups campaign skip removed, retry prior lost, no reply-turn claim) each failed 1-5 tests. WA subset 561 passed; full offline suite 1438 passed, 126 skipped, 51 deselected. Deploy: restart pflege-wa.service before any campaign (old process drops statuses, parses taps without payload); no new unit/env; docs/whatsapp.md 'Campaign sender (TASK-103)' + runbook, docs/rollout-runbook.md step 7. Nothing sent, no Graph call, data/wa.sqlite not opened.

Fixer 2026-09-14 (review F2/R2/F2 window, F7 stale claim): send_one takes the window and checks window.is_open(clock()) inside the claim transaction, after the history import; closed -> deferred reason outside_window, run_send goes back to the loop top (waits for the next start, --no-wait exits 3); claimed_at is that clock value; progress line prints sent_at/claimed_at. _in_flight_until only counts claims younger than STALE_CLAIM_SECONDS (same cutoff as ST.claim_in_flight). Tests tests/test_wa_campaign_sender.py: window checked again after a 5-minute import at 20:58 (no send before 09:00 next day, import re-run, claim 09:05), --no-wait stops outside_window with no claim/ownership, stale import claim next to a live claim keeps retry_after ~300 s ahead. Docs: send step 2, pacing paragraph.

Fixer 2026-09-14 (review F6 sender): phone_state this_campaign carries delivery (campaign.delivery_view, shared with --status); decide() plans delivery_failed (PROBLEM_ACTIONS, exit 1, codes in the reason) for a sent claim whose latest status is failed; resend under a new --campaign-id. Test test_a_template_meta_reports_undelivered_is_reported_not_resent_and_goes_out_under_a_new_campaign (131049 -> re-run delivery_failed + already_sent, no POST; new campaign id sends and replaces card.campaign). Docs: plan actions. Open for Ivan: auto-retry delivery failures, and whether ownership should return after one.

Fixer 2026-09-14 (review R3, uncertain reconcile): --mark-sent PHONE=WAMID (needs --template-id; campaign.mark_sent/run_mark_sent, store.reconcile_campaign_send): claim in_progress/uncertain and its campaign:<id> claim stale, same template, wamid not stored, a status of that wamid for the phone since the claim, no status naming a category other than the template's, template renders the claimed text -> record_campaign_send + claim sent (sent_at = earliest status time) in one transaction; refusals reported, exit 1. --status statuses_since_claim_without_message carry category (status_category). Tests: timeout -> uncertain -> sent status (marketing) + old utility receipt -> utility and unknown wamid refused -> mark-sent -> claim/row/card.campaign, re-run already_sent, button tap reaches Luna with card.campaign and replies_to found; a crashed in-flight claim is refused until stale, a sent claim is refused. Updated the crash test's expected status dict (category). Docs: usage block, runbook Stop/Uncertain, --status paragraph.

Fixer 2026-09-14 (review F3 old-system opt-outs): with --import-history-*, a phone whose imported history has stop_messages is skip_opted_out, in the dry-run preview (plan) and again after the --apply import right before the claim (send_one); _import_view carries stop_messages. Test test_a_stopp_in_the_old_systems_history_is_never_sent (dry-run + send, no POST/claim/ownership for that phone). Without a history source it stays invisible (documented); the old system's closed_reason declined_opt_out / outreach declined are not imported -> open question for Ivan. Docs: plan actions.

Repair round 1 2026-09-14: (1) exit code -- main() now returns 3 whenever a --no-wait run stopped before the list was done and no send of this run failed/was uncertain/import error; problem leads of the plan (invalid_phone, variables_error, conflicting_duplicate, uncertain, delivery_failed) still print and are reported every run and give 1 once the list is done or in a dry-run. Before: one such lead (e.g. any delivery_failed after 131049) made every stop exit 1, so a run could not tell whether the list was done. Test test_a_problem_lead_does_not_hide_that_a_no_wait_run_stopped_before_the_list_was_done (3 then 1); module docstring EXIT and docs/whatsapp.md Report updated. (2) .gitignore: data/known-real-system-phones.txt (hourly TASK-87 export of real-system phones, untracked and not ignored -> PII commit risk). data/wa_test_docs/ is TASK-95's synthetic generator output, left alone.

Final verification 2026-09-14 20:45-20:57 UTC (after 4-lens review, adversarial verify, fixer + 2 repair rounds): offline suite 1474 passed, 126 skipped, 0 failed. pflege-wa.service restarted 21:22 UTC on this tree; health webhook_ready/outbound_ready/luna_ready true, threads 200. Nothing sent; campaign not run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
python -m app.wa.luna.campaign sends an approved template to a lead list ourselves: dry-run by default, durable per-phone claims, ownership flipped before the send and restored on synchronous errors, rendered text recorded and card.campaign seeded, batches of 10 every 12 min within 09-21 Europe/Berlin, resumable, --status with delivery statuses. Verified by 32 offline tests and an e2e run (fake Meta, real claude CLI) covering dry-run, pacing/window, failed and delivered statuses, a button yes and a decline reaching Luna, and follow-ups. Runbook requires a delivered probe first (billing 131042).
<!-- SECTION:FINAL_SUMMARY:END -->
