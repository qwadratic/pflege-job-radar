---
id: TASK-105
title: Skip campaign phones the old system recorded as declined or opted out
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 22:15'
updated_date: '2026-09-15 01:31'
labels: []
dependencies:
  - TASK-102
  - TASK-103
type: feature
ordinal: 105000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-14: opt-outs and declines recorded by the old system must be honoured, not only a typed Stopp found in the imported chat history. The old system records them beyond chat text, e.g. candidate lifecycle closed_reason declined_opt_out (apps/connectors/candidate_lifecycle.py, candidate_goal_orchestrator.py) and outreach status declined (job_wohnung_outreach). Today import_history.py only derives stop_messages from chat bodies, and the campaign sender only sees them when --import-history-* is given.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 the history import reads the old system opt-out/decline records read-only (operator SQL documented in deploy/import-history.example.sql, written from old-system code) and reports per phone which record and when
- [x] #2 the campaign dry-run and --send never send to a phone with such a record or a Stopp in its old history, reporting it as skipped with the reason; the check runs for every send (an explicit, logged override flag is the only way to send without the old-system source)
- [x] #3 an imported decline marks the thread declined (TASK-101 semantics: no follow-ups, silence) so Luna does not treat a later message as a fresh lead without re-engagement
- [x] #4 offline tests with the synthetic old-schema fixture cover lifecycle opt-out, outreach decline, chat Stopp, and the override flag; docs and runbook updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Old-system code read (no data): where declines/opt-outs are persisted. candidates.metadata_json.lifecycle {status closed, reason declined_opt_out, closed_at} (candidate_lifecycle.persist_candidate_lifecycle); candidates.metadata_json.bayern_housing_offer {status declined, replied_at} (template No, candidate_bayern_housing_offer.apply_offer_reply_to_meta); job_wohnung_outreach.status='declined' (replied_at; candidate_job_wohnung_outreach.mark_outreach_declined); suppression_list phone rows (channel phone/all/*/'' or payload channels phone/all; candidate_suppression.find_phone_suppression_matches, normalize_phone); candidate_recruitment_state.placement_stage='withdrawn' (manager call outcome not_interested). All in sales_brain.sqlite.
2. import_history.py: fifth required query opt_outs (source_ref, kind opt_out|decline, at ISO 8601, reason required; phone optional = the source's stored value). Report opt_outs per phone (sorted by at) next to stop_messages; found counts them. --apply: card.prior_opt_outs (records + chat Stopps, per source_ref once, code-owned) and card.declined/declined_reason/declined_at (TASK-101) from new records, unless the card is already declined or the candidate wrote here (last_inbound_at) or re-engaged (re_engaged_at) after the latest record. Dry-run previews the same decision. prior_contact.summary names the records.
3. deploy/import-history.example.sql: opt_outs query for the five record kinds, from old code only; comments on scope.
4. campaign.py: history source required for dry-run and --send; --override-no-history-source is the only way without it (refused together with a source), printed as WARNING and recorded in report.history_source. plan() and send_one(): any opt_out record or chat Stopp -> skip_opted_out, only decline records -> skip_declined, reason lists source, kind, at, reason, source_ref.
5. luna_brain CODE_OWNED_CARD_KEYS + prior_opt_outs; prompt DECLINE: card.declined may come from prior_opt_outs (no ack in this chat), same rule.
6. Tests: synthetic old schema gets suppression_list/job_wohnung_outreach; import tests (lifecycle opt-out, outreach decline, housing-offer decline, withdrawn, suppression phone normalisation, contract violations, declined marking and TASK-101 silence/re-engagement, no re-mark after re-engagement, not marked when the candidate wrote here later); campaign tests end to end with the real example queries (lifecycle opt-out, outreach decline, chat Stopp, clean lead; dry-run and send) plus the override flag (missing source exit 2, override logged).
7. Docs: whatsapp.md (contract, sender, runbook), rollout-runbook.md steps 6/7. Full offline suite once.

Review fix 2026-09-15 (R4): docs/rollout-runbook.md step 7 block exited 2 as written (no history source); H defined there as in docs/whatsapp.md and appended to probe, dry-run, send and retry commands.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (offline, not committed):
- Old-system code read with sudo -n grep/cat of .py/.sql only, no database opened. Persisted records found: candidates.metadata_json.lifecycle (status closed, reason declined_opt_out, closed_at, reopen_after; candidate_lifecycle.persist_candidate_lifecycle; its only in-code caller is sync_portfolio_lifecycle, wa_inbox classifies on the fly, the old Job+Wohnung campaign skipped any persisted closed lifecycle), candidates.metadata_json.bayern_housing_offer status declined (template No), job_wohnung_outreach.status declined (sticky), candidate_recruitment_state.placement_stage withdrawn (manager call not_interested), suppression_list phone rows (normalize_phone match). All in sales_brain.sqlite.
- import_history.py: fifth query opt_outs (source_ref, kind opt_out|decline, at ISO 8601, reason required; phone optional). Report opt_outs + decline_import; contact_blocks() = records + chat Stopps; decline_decision() marks card.declined/declined_reason/declined_at from the latest new record unless already declined or the candidate wrote here / re-engaged after it; card.prior_opt_outs (code-owned in luna_brain); prior_contact.summary names the records.
- deploy/import-history.example.sql: opt_outs query for the five record kinds.
- campaign.py: source required for dry-run and --send, --override-no-history-source otherwise (WARNING on stdout+stderr, report.history_source); history_skip() -> skip_opted_out / skip_declined with every record in the reason; skip_declined reason now carries declined_reason.
- prompts DECLINE: card.declined may come from prior_opt_outs (no ack sent here), same rule.
- Tests: import 38 passed (5 new: records per phone incl. suppression normalisation and reopened lifecycle, apply marks declined + silence + no follow-up + re-engagement + no re-mark, older decline vs later message here, own decline kept, contract violations; stop test extended), sender 44 passed (2 new: end to end with the synthetic old DB and the example queries, override flag). Mutation checks: disabling the mark or the skip fails the new tests.

Docs: docs/whatsapp.md (Decline paragraph, Campaign recipients contract + opt_outs record table + what --apply writes, Campaign sender history source/override, plan actions, send step 1, exit 2, runbook commands with the source, tests), docs/rollout-runbook.md steps 6/7.
Live llm (claude CLI, fake Meta, tmp SQLite, nothing sent): new test_an_imported_old_system_opt_out_stays_silent_until_the_candidate_re_engages [1] passed, [2] passed (a second [2] run printed the silent first turn, result not captured); 'Ok, danke.' -> no reply, re-engagement -> 'Schön, dass Sie sich wieder melden! Ich bin Valentina von der NDT Group.' + Urkunde question. Regression after the DECLINE prompt change: test_decline_by_text_then_ok_danke_stays_silent[1] and test_re_engagement_after_a_decline_resumes_the_funnel[1] passed.
Offline: full suite once, 1510 passed, 126 skipped, 61 deselected (137 s); afterwards two assertions added (CLI output line, metadata-phone evidence) and the four touched files re-run: 281 passed.
Open for Ivan: employed_elsewhere / explicit_closed_flag lifecycle reasons not read; suppression rows of every workspace count; placement withdrawn counts as a decline; old correspondence-derived declines (regex on last inbound) are not re-derived; an imported Stopp declines (re-engageable) rather than stops the thread; live sales_brain.sqlite schema never inspected (tables from code), a missing table fails loudly as import_error.

Review fix 2026-09-15 (R4): docs/rollout-runbook.md step 7 block exited 2 as written; it now defines H (history source, same as docs/whatsapp.md) and appends $H to the probe --send, real-list dry-run, --send and both --retry-delivery-failed commands; --status unchanged (returns before the check).

Final verification 2026-09-15 (~01:20 UTC, after review + adversarial verify + fixer): offline suite 1571 passed, 126 skipped, 0 failed. Live llm, one at a time: campaign full funnel 3/3 non-empty shortlist, flexible funnel 1/1 (5 clinics), imported opt-out silence then re-engagement 1/1, voice-note reply from transcript 1/1 (fake STT), misheard town asked back 1/1. Follow-up fix 01:30 UTC: the unmatched-department ToolError no longer carries candidate-facing English (a run had copied it into a bubble and broken JSON); tests/test_wa_luna_tools.py 15 passed, Urologie funnel llm 2/2. Not yet deployed: pflege-wa.service restart pending Ivan's go.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The history import reads old-system opt-outs and declines (lifecycle declined_opt_out, outreach/housing-offer declines, withdrawn, suppression) via operator SQL, the campaign sender skips such phones and chat Stopps on every dry-run and send (explicit logged override only), and an imported decline puts the thread into TASK-101 silence until re-engagement. Verified on the synthetic old-schema fixture, a live llm silence/re-engagement run and the full offline suite; the example SQL is not yet checked against the real old DB.
<!-- SECTION:FINAL_SUMMARY:END -->
