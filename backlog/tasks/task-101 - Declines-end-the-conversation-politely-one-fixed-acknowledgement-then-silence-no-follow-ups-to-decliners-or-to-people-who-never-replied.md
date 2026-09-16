---
id: TASK-101
title: >-
  Declines end the conversation politely: one fixed acknowledgement, then
  silence; no follow-ups to decliners or to people who never replied
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 14:24'
updated_date: '2026-09-16 14:13'
labels: []
dependencies: []
type: feature
ordinal: 101000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live checks 2026-09-14: after "Nein danke, habe schon eine Stelle" the follow-up timer would still send "sind Sie noch da?" up to 3 times (TERMINAL_STAGES only has consented/not_placeable); campaign recipients who never replied would get free-text nudges outside the 24h window (_freeform_window_open treats no inbound as open), which Meta does not deliver; a no_send turn leaves the ball on us so catch-up re-runs the model on the same "Danke" every 3 min up to the hourly cap. Ivan 2026-09-14: on a decline send one polite fixed reply (old bot wording: "Alles klar, vielen Dank für die Rückmeldung. Falls sich das ändert, schreiben Sie mir gern.") and then stay silent; non-responders get nothing further.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 a decline (template No button or a clear refusal in text) sends the fixed acknowledgement exactly once and marks the thread declined; later messages that do not re-open interest get no reply, while a later clear re-engagement resumes the funnel
- [x] #2 declined (and already-placed without new interest) threads are terminal for follow-ups; threads with no inbound message ever get no free-text follow-up, and the free-form window is treated as closed when the candidate never wrote
- [x] #3 a no_send decision is recorded so catch-up does not re-run the model on the same inbound message
- [x] #4 offline tests and an llm persona run cover decline by button, decline by text, re-engagement after decline, non-responder, and the catch-up no_send case; docs updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Output schema: decline, decline_reason, re_engaged. luna_brain.turn: first decline -> bubbles=[prompts.DECLINE_ACK_DE] action decline_ack (row meta.action=decline_ack via send), card declined/declined_reason/declined_at (code-owned, stripped from card_patch); declined thread -> no_send unless re_engaged (then declined cleared, re_engaged_at, model bubbles). STOP stays a code stop without ack.
2. Recorded no_send: claim state skipped_no_send becomes final in store.claim_reply_turn; process_owed_turn returns no_send_recorded without claiming/calling the model when the inbound already has it (drain_pending then drops the pending row); reporting.ball_for -> 'silent' for a last inbound with recorded no_send; shadow_run.phones_owed_a_reply excludes it (catch-up owed pass reads that). catchup.py untouched.
3. reporting.stage_for: 'declined' and 'already_placed' (already_placed without open_to_new_position) stages; followups.TERMINAL_STAGES includes them; followups skip threads without any inbound row.
4. api._freeform_window_open -> False when last_inbound_at is unset; check callers (first reply path sets last_inbound_at in _note_arrival first; tests that call process_owed_turn/_send on bare threads get an inbound).
5. Tests offline (brain, followups, reporting, process_owed_turn, store claims, harness window) + llm personas (decline button, decline text then silence, re-engagement, already placed); docs.

Fixer round (review 2026-09-14): 1. a consent Nein danke tap is never a decline (prompt DECLINE/CONSENT; turn() ignores decline on consent:no); llm persona test. 2. region shortcut skipped on declined/campaign threads. 3. media nothing reads: a declined card records a no_send instead of MEDIA_REPLY; every MEDIA_REPLY promise records card._unread_media + _escalated, shown in /wa/threads and campaign --status; follow-ups skip a thread whose last inbound is unread media.

Repair round 1 (final verifier 2026-09-14): decline persona tests run on the approved template's real 'Nein, kein Interesse' button (payload = label, no synthetic payload); llm runs recorded.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-14: Implemented decline flag -> prompts.DECLINE_ACK_DE once (action decline_ack), card.declined/declined_reason/declined_at code-owned, declined_no_send until re_engaged; NO_SEND_STATE (skipped_no_send) final in claim_reply_turn, process_owed_turn no_send_recorded pre-check, ball_for 'silent', phones_owed_a_reply excludes it; stages declined/already_placed; followups TERMINAL_STAGES + skip never-wrote; _freeform_window_open False without inbound. Adjusted fixtures that relied on the old behaviour: test_wa_harness window test, test_wa_process_owed_turn (_arrived helper), test_wa_luna_followups (_seed_them writes an inbound by default), test_wa_luna_shadow_run (last_inbound_at), test_wa_store_claims (skipped_no_send final).

REAL CAMPAIGN TEMPLATE (approved 2026-09-14, read-only lookup on WABA <WABA id>): id 1791710088522158, name recruitment_bayern_stellen_interesse_de, language de, MARKETING, parameter_format POSITIONAL. HEADER text "Neue Stellen in Bayern für Pflegekräfte"; BODY "Hallo, {{1}}. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir viele neue Stellen in Bayern. Haben Sie noch Interesse?" ({{1}} = candidate name); QUICK_REPLY buttons "Ja, ich habe Interesse" and "Nein, kein Interesse" (a tap arrives as type=button with that text/payload). The No button is a decline (TASK-101: fixed ack once, then silence); the Yes button is interest in Bayern (TASK-100). Test sends at 15:46 UTC to two test numbers (old-system test candidate id 14 and Ivan) were accepted by Meta; they were not recorded in wa.sqlite. Phone number verified_name is "Valentyn NDT".

2026-09-14: llm decline by button x2 and decline by text then 'ok danke' (silent, ball silent) x2 passed. Full offline suite 1374 passed.

2026-09-14 llm: decline by button, decline by text + 'ok danke' (silent), re-engagement -- round 1 6/6, round 2 6/6 (after 'no region on decline' rule). process_owed_turn writes the turn marker only after the claim is final.

2026-09-14 llm final round: decline by button x2, decline by text + 'ok danke' silent x2 (ball silent, stage declined), re-engagement x2 -- all passed; no region recorded on a decline after the rule change.

Fixer 2026-09-14 (review R1 consent No, R2/F1 region): prompts DECLINE says a consent 'Nein danke' tap is not a decline, CONSENT says what to reply after it; luna_brain.turn ignores the decline flag on consent:no (consent_no_tap), the model's reply goes out. Out-of-scope shortcut (_region_shortcut_applies) only for typed text on threads without card.campaign/card.declined, the model decides there; CAMPAIGN rule says how to read a named Land. Tests tests/test_wa_luna_campaign.py: consent no tap never a decline; decline naming Hessen on a campaign thread -> ack once, declined, no region, FU.run nudges nothing; Land on a declined thread -> no reply; 'Ja, wohne aber in NRW' reaches the model; typed Land on an ordinary thread keeps the locked text. Mutation (shortcut always on) fails 3. Docs: decided-in-code list, Decline paragraph.

Fixer 2026-09-14 (review F4/R3, voice notes): api._media_ack records every MEDIA_REPLY promise for a human first (card._unread_media {wamid, kind, document_id, received_at}, _escalated); a declined Luna card gets no MEDIA_REPLY (claim NO_SEND_STATE, action declined_no_send, ball silent). followups skip a thread whose last inbound is unread media (store.last_inbound); GET /wa/threads rows carry unread_media; campaign --status rows unread_media since the send + totals.unread_media; _unread_media is code-owned; prompt OTHER MESSAGE KINDS tells Luna never to claim it heard one. Tests: tests/test_wa_luna_campaign.py voice note on a declined thread (no reply, flagged, silent), voice-note reply to the campaign (MEDIA_REPLY, /wa/threads unread_media, FU.run nudges nothing; a typed message afterwards is nudgeable again); tests/test_wa_campaign_sender.py voice note in --status. Open for Ivan: transcription, or a MEDIA_REPLY that also asks to type the answer.

Fixer 2026-09-14 llm (tests/test_wa_luna_campaign_personas.py, real model, handle_payload + fake Meta): new test_consent_no_tap_is_not_a_decline (campaign Ja, seeded facts, CV + Urkunde uploads, close turns, consent 'Nein danke' tap): 3 valid runs passed -- replies like 'Alles klar, kein Problem – ohne Ihre Zustimmung leiten wir nichts weiter. | Melden Sie sich gern jederzeit wieder, falls Sie es sich anders überlegen oder noch Fragen haben.', anonymous_send_consent=false, declined unset; a 4th run failed before the tap when the model answered the Urkunde upload turn with plain text instead of JSON (existing parse failure, unrelated). re-engagement after decline x2 passed with the intro assertion.

Repair round 1 2026-09-14: decline persona tests now run on the approved template's real buttons (tap 'Nein, kein Interesse' with the label as payload, as Meta sends it when the send has no payload param; before: synthetic 'Nein, danke'/bayern_no). llm on the final prompt: No button 2/2 exactly DECLINE_ACK_DE with declined=true and no region; text decline + 'ok danke' silence 2/2; re-engagement 2/2 (intro + Urkunde question); consent 'Nein danke' tap not a decline 2/2. The decline itself stays the model's flag (AC #1 allows it).

Repair round 2 2026-09-14: no code change here. Open for Ivan (final verifier problems 2, 9): the template No tap is a decline only through the model's flag (payload fixed as tpl:Nein, kein Interesse; previous rounds kept the flag, AC1 allows it); every later message on a declined thread costs one model call to detect re_engaged. Model variance: a bare template Ja set open_to_new_position=true once (1/2); it only matters if already_placed comes later (stage then not terminal, follow-ups could nudge), and an employed candidate open to a change is a real reading of both flags, so no code rule was added.

Final verification 2026-09-14 20:45-20:57 UTC (after 4-lens review, adversarial verify, fixer + 2 repair rounds): offline suite 1474 passed, 126 skipped, 0 failed. pflege-wa.service restarted 21:22 UTC on this tree; health webhook_ready/outbound_ready/luna_ready true, threads 200. Nothing sent; campaign not run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A decline gets the old bot's fixed acknowledgement once and then silence, with re-engagement resuming the funnel; declined/already-placed and never-replied threads get no follow-ups; a no_send decision is recorded so catch-up never re-runs the model. Live llm: No button 2/2, text decline then 'ok danke' silent 2/2, re-engagement 2/2, consent 'Nein danke' not a decline 2/2; campaign e2e showed follow-ups only to the one replier. Decline detection relies on the model flag (open question for Ivan).
<!-- SECTION:FINAL_SUMMARY:END -->
