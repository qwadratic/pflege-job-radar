---
id: TASK-100
title: >-
  Luna sees outbound it did not write (campaign template, nudges), keeps button
  payloads and reply context, and introduces itself like the old bot
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 14:24'
updated_date: '2026-09-16 14:13'
labels: []
dependencies: []
type: feature
ordinal: 100000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live llm checks 2026-09-14: on a reply to a campaign template Luna greeted as if the candidate wrote first and re-asked Bayern (9/9 first turns) because the model payload has no record of our template; on a phone with an existing Luna session a "Ja" to the template (or to a follow-up nudge) was read as the answer to the last Luna question (false Urkunde). parse_message drops the template quick-reply payload and context.id, so a button tap is indistinguishable from typed text. Asked who we are, Luna invented a brand ("pflege-job-radar", from constitution owner_note). Ivan 2026-09-14: introduce as the old bot does (Valentina von NDT); campaign design approved.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 a campaign-opened thread carries campaign context on the card (template name, rendered text, buttons, sent_at) and Luna does not greet as first contact, treats a yes as interest in Bayern, and continues with the next open gate
- [x] #2 every outbound message Luna did not author since its last turn (templates, follow-up nudges, fixed acks) reaches the model with its text and time, and the latest inbound is interpreted as answering that message; no card fact is set from a reply to it unless the reply states it
- [x] #3 template button taps keep their payload (namespaced so it can never collide with consent buttons) and every inbound keeps the replied-to wamid; the model is told when a reply is a button tap on a template
- [x] #4 identity answers use the same persona and company naming as the old bot, never an invented brand or data source
- [x] #5 offline tests plus llm persona runs of: template Ja text, template button, who-are-you, already-placed, existing-session Ja, nudge Ja; docs updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Old bot wording confirmed from code: 'Ich bin Valentina von der NDT Group.' (first touch, candidate_reply_council greeting), 'Ich bin Valentina — ein digitaler Assistent der NDT Group.' (candidate_locked_phrases HONEST_AI_IDENTITY_DE); campaign body 'Sie hatten sich früher bei uns gemeldet'.
2. store.py (appended): record_campaign_send(c, phone, wamid, rendered, campaign_id) = the one seed of card.campaign {campaign_id, template_name, language, rendered_text, buttons, sent_at, wamid} + the outbound template row (meta.action=campaign) + last_outbound_at, one commit; message_by_wamid, messages_for, has_inbound.
3. api.parse_message: template 'button' keeps payload as button_id 'tpl:<payload>', text kept; every kind keeps context.id as reply_to_wamid (+ raw context) in meta.
4. luna_brain.turn_context(c, t, turn_key): outbound_since_last_turn = outbound rows after the card marker _luna_last_turn {at, seen_through_id, own_message_ids} not authored by the model's own bubbles (legacy session without marker: rows after the last Luna-turn row); last_turn_at; reply_context {kind, is_template_button, template_button_payload, received_at, replies_to (stored row or found=false)}. process_owed_turn passes it on t['turn_context'] (LB.turn signature unchanged) and writes the marker after the send (own rows = model bubbles by body+action).
5. Payload fields + prompt: THINK_ORDER 1 exception and 4 amendment, rules OUR OUTBOUND / CAMPAIGN / TEMPLATE BUTTON / IDENTITY (NDT Group, contacted this number before, Stopp, human) / ALREADY PLACED (card_patch already_placed, open_to_new_position); remove pflege-job-radar from constitution owner_note; MCP server name pflege_board -> neutral name (model-visible tool prefix).
6. shadow_run.shadow_turn passes the same context. Tests offline + llm persona file; docs whatsapp.md + VENDORED.md.

Fixer round (review 2026-09-14): 1. parse_message answers reaction/sticker/location/contacts/unsupported as inbound kinds with a text summary and reply_to_wamid (reaction.message_id). 2. turn_context: latest delivery status on outbound views; failed rows and a failed card.campaign stay out of the model payload. 3. 'introduced' like the old bot (_has_valentina_freeform_greeting) drives the CAMPAIGN self-introduction instead of fresh_session. 4. out-of-scope region shortcut: typed text only, skipped on campaign and declined threads (the model decides). Tests, docs.

Repair round 1 (final verifier 2026-09-14): (a) ALREADY PLACED: ask about openness to a position now, never offer/promise to send positions later or keep them informed (nothing here writes unprompted); persona test asserts no such promise. (b) Nudge Ja wrong speaker ('ich bin noch da'): say positively what to write (they are back), persona test asserts no 'ich bin (noch) da/hier'. (c) Identity wording: GOAL/IDENTITY use the old bot's 'ein digitaler Assistent der NDT Group' (old code has no 'Assistentin'); offline prompt test + persona assertion. (d) Persona tests use the approved template recruitment_bayern_stellen_interesse_de (header/body/buttons as live-fetched) with body-only params like the sender, taps carry payload = label as Meta does without a payload param. Each changed llm test run repeatedly with evidence.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-14: Implemented store.record_campaign_send (card.campaign contract), api.parse_message tpl:<payload> + reply_to_wamid/context, luna_brain.turn_context/turn_marker (marker _luna_last_turn on the card; legacy sessions derive the last model row), payload fields outbound_since_last_turn/last_turn_at/reply_context/fresh_session, is_button_reply false for template taps, prompt rules (THINK 1/4, IDENTITY NDT Group, OUR OUTBOUND, CAMPAIGN, TEMPLATE BUTTON, ALREADY PLACED), constitution owner_note/identity/region, MCP server renamed jobs (tool prefix model-visible). shadow_run passes the same context. Offline: tests/test_wa_luna_campaign.py 34 passed; WA offline suite 496 passed.

REAL CAMPAIGN TEMPLATE (approved 2026-09-14, read-only lookup on WABA <WABA id>): id 1791710088522158, name recruitment_bayern_stellen_interesse_de, language de, MARKETING, parameter_format POSITIONAL. HEADER text "Neue Stellen in Bayern für Pflegekräfte"; BODY "Hallo, {{1}}. Sie haben sich als Pflegekraft in Bayern beworben. Aktuell haben wir viele neue Stellen in Bayern. Haben Sie noch Interesse?" ({{1}} = candidate name); QUICK_REPLY buttons "Ja, ich habe Interesse" and "Nein, kein Interesse" (a tap arrives as type=button with that text/payload). The No button is a decline (TASK-101: fixed ack once, then silence); the Yes button is interest in Bayern (TASK-100). Test sends at 15:46 UTC to two test numbers (old-system test candidate id 14 and Ivan) were accepted by Meta; they were not recorded in wa.sqlite. Phone number verified_name is "Valentyn NDT".

2026-09-14: docs/whatsapp.md section 'Campaign replies, our outbound, declines (TASK-100/101)' + persona/tools/window/stage/followup lines; VENDORED.md identity update. Deterministic brain reads a template tap as its label (process_owed_turn, shadow_run). Full offline suite: 1374 passed, 126 skipped. llm group 1 (campaign Ja text, yes button, who-are-you, decline button, decline text + ok danke; each twice): 10 passed.

2026-09-14 llm round 1 (20 runs, 10 scenarios x2): 20 passed. Round 2 after prompt tweaks (nudge Ja = candidate still there; decline records no region): 18/20 -- 2 campaign-yes replies quoted the open-jobs count ('Aktuell haben wir 6 offene Pflegestellen in Bayern'). Prompt now forbids quoting market_snapshot.open_jobs in the reply to the template (THINK 1 exception + CAMPAIGN); round 3 running. Decision: on a fresh session the first reply to a campaign names Valentina once ('Ich bin Valentina von der NDT Group.'), as the old bot's first touch after a campaign yes does (configs/recruitment_funnels/whatsapp_ad_first_touch_abc_v1.json variant B); no welcome/count/region question.

2026-09-14 llm final round (final prompt, fixture board, handle_payload + fake Meta): 20/20 -- campaign typed Ja x2, yes button x2, who-are-you x2, already placed x2, stalled session + template + Ja x2, nudge + Ja x2, campaign Ja to shortlist x2 (+ decline scenarios). Fixes from earlier rounds: no open-jobs count in the reply to the template; nudge Ja = candidate still there (1/6 runs still said 'ich bin da', no card fact in any run); already-placed yes/no detector reads the question after the last dash. Regression tests/test_wa_luna_personas.py: 16/17; the needless search_postings test is ~50% flaky at the old prompt as well (throwaway A/B: old prompt 3/6 runs call search_postings; server name pflege_board vs jobs vs tools no clear effect).

Fixer 2026-09-14 (review R2/F1): the out-of-scope region shortcut no longer answers campaign threads ('Ja, wohne aber in NRW' re-asked Bayern after a yes to the Bayern template); see TASK-101 note for tests.

Fixer 2026-09-14 (review F3/R4, reactions): api.parse_message answers SUMMARIZED_KINDS (reaction, sticker, location, contacts, unsupported/unknown) from a text summary with the raw object in meta; a reaction's reply_to_wamid is reaction.message_id, text = emoji or '[reaction removed]'. They get a pending row and a turn, count as replies (has_inbound, --status, follow-ups); the region shortcut ignores location/contacts. Prompt OTHER MESSAGE KINDS. Tests: tests/test_wa_luna_campaign.py (parse per kind, unsupported keeps errors, a 👍 on the campaign reaches the model with replies_to the template, a Berlin location pin records no region), tests/test_wa_campaign_sender.py test_a_thumbs_up_on_the_template_is_a_reply_luna_answers (router -> Luna -> --status replies 1, to_the_template). Updated: test_wa_harness parse test and router raw-message test now use a 'system' message. Docs: Inbound parsing paragraph.

Fixer 2026-09-14 (review F6 context, R5 intro): luna_brain.turn_context gives every outbound view its latest delivery {status, error_codes}; rows whose latest status is failed are left out of outbound_since_last_turn, and campaign_delivery_failed makes _user_payload drop card.campaign from the payload card (the card keeps it). New payload field introduced = luna_brain.introduced(): a text/buttons/draft outbound matching 'ich bin valentina|ndt group' or '^hallo (frau|herr)' (old bot _has_valentina_freeform_greeting); prompt CAMPAIGN introduces while introduced is false, for every kind of reply, instead of fresh_session. Tests tests/test_wa_luna_campaign.py: undelivered template out of the payload, delivered template carries delivery (outbound + replies_to), introduced after decline -> re-engagement [False, False, True] with fresh_session [True, False, False], introduced() per outbound kind/body. Docs: payload table, prompt paragraph. Persona assertions for the intro: see llm test notes.

Fixer 2026-09-14 llm: intro assertions (INTRO_RE Valentina + NDT) added to campaign typed Ja, yes button, already placed, re-engagement; new test_campaign_thumbs_up_reaction_continues_with_the_urkunde_question. 10/10 passed: every first reply named 'Ich bin Valentina von der NDT Group' (already placed: 'Das freut mich für Sie, herzlichen Glückwunsch zur neuen Stelle! Ich bin übrigens Valentina von der NDT Group. | Wären Sie trotzdem offen, mal von einer neuen Position bei uns zu hören?'); 👍 reaction x2 -> 'Ich bin Valentina von der NDT Group. Schön, dass Sie Interesse haben! 😊 | Haben Sie bereits die deutsche Pflege-Urkunde ...?', region=Bayern. Observed, not asserted: one re-engagement reply quoted the open-jobs count ('Aktuell haben wir 6 offene Pflegestellen in Bayern').

Repair round 1 2026-09-14 (final verifier problems 4, 5, 7, 14): prompts.py -- GOAL 'Du bist Valentina, ein digitaler Assistent der NDT Group' and IDENTITY 'in the old bot's words ... ein digitaler Assistent der NDT Group (never Assistentin)' (old code has only 'ein digitaler Assistent', candidate_locked_phrases.py; GOAL said 'die digitale Assistentin'). OUR OUTBOUND: a nudge Ja gets at most a few words that they are back, never a word about yourself being there ('ich bin (noch) da/hier'); verifier saw 'Alles gut, ich bin noch da' 1/2. ALREADY PLACED: congratulate (+ intro while introduced is false, CAMPAIGN), ONE yes/no whether they would still like to look at the positions open in Bayern now; never a later/conditional frame, never offer to send positions later or keep them informed (nothing writes to an already-placed thread). tests/test_wa_luna_campaign_personas.py now uses the approved template recruitment_bayern_stellen_interesse_de (components as the live lookup returned them), body-only params like the sender, taps carry the label as payload (Meta without a payload param); new assertions SELF_THERE_RE (nudge), PROMISE_LATER_RE (already placed), no 'assistentin' (who-are-you). Offline prompt test extended. llm evidence: first ALREADY PLACED wording 2/4 promise frame ('Falls sich doch noch etwas Interessantes in Bayern ergeben sollte', 'falls sich etwas Passendes ergibt') and intro dropped 1/4; second 5/6 (intro dropped once); final wording: full persona file 24/24 on the approved template, extra already placed 4/4 + nudge 4/4 (already placed 6/6, nudge 6/6 in total), import reuse personas 4/4. Sample: 'Das freut mich sehr für Sie, herzlichen Glückwunsch zur neuen Stelle! Ich bin Valentina von der NDT Group. | Möchten Sie trotzdem einen Blick auf die aktuell offenen Stellen in Bayern werfen?'; nudge Ja -> 'Schön, dass Sie sich melden! | Haben Sie bereits die deutsche Pflege-Urkunde?'; who-are-you -> 'Ich bin Valentina von der NDT Group, ein digitaler Assistent. Sie hatten sich zuvor als Pflegekraft bei uns beworben ...'. Residual variance: 1/6 final already placed runs appended 'falls sich später etwas ändert' to the look-now question (no promise). Offline suite 1474 passed, 126 skipped.

Final verification 2026-09-14 20:45-20:57 UTC (after 4-lens review, adversarial verify, fixer + 2 repair rounds): offline suite 1474 passed, 126 skipped, 0 failed. pflege-wa.service restarted 21:22 UTC on this tree; health webhook_ready/outbound_ready/luna_ready true, threads 200. Nothing sent; campaign not run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Luna knows a thread was opened by our template (card.campaign) and sees every outbound it did not write since its last turn; template button taps keep their payload and reply context; identity matches the old bot (Valentina von der NDT Group). Live llm: template button 2/2, typed Ja 3/4 (one run quoted the open-jobs count), who-are-you 2/2, already placed 2/2, existing session and nudge Ja 2/2 each, full funnel 3/4 (department_pref 'flexibel' variance). Plus offline tests and the full suite.
<!-- SECTION:FINAL_SUMMARY:END -->
