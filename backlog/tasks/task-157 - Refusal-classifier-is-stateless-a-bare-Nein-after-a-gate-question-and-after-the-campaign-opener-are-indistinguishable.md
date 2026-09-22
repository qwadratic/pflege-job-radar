---
id: TASK-157
title: >-
  Refusal classifier is stateless: a bare Nein after a gate question and after
  the campaign opener are indistinguishable
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 10:13'
updated_date: '2026-09-22 10:31'
labels: []
dependencies: []
ordinal: 165000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-156 wired app/wa/luna/refusal.py's classifier into luna_brain.py's decline branch. Verification on 2026-09-22 measured it against the live classifier: 'Nein, kein Interesse' and 'Nein danke, habe schon eine Stelle in Hessen' correctly come out as refusals, but a bare 'Nein' and 'Nein, danke' do NOT -- because the classifier is stateless and is only given the candidate's reply, never the question it answers. 'Nein' after the campaign opener (is your job search still relevant?) is a refusal; 'Nein' after a gate question (Urkunde, Bayern, a city, housing) is an answer. This matters beyond one extra polite message: card.declined is what app/wa/luna/followups.py keys on (TERMINAL_STAGES via reporting.stage_for) -- a terse decliner who is misclassified stays eligible for follow-up nudges, which is exactly what TASK-101/TASK-105 exist to prevent. The fix is a missing input, not a policy change: give the classifier what the bot last asked or offered on this thread, short, so 'refusing the conversation' vs 'answering the question we asked' becomes decidable. Ivan's rule stands unchanged -- every classifier failure mode still lands on NOT a refusal (keep talking).

Also to repair, both side effects of the TASK-155/156 work, both outside the lane that caused them:
- tests/test_wa_luna_brain.py::test_too_many_bubbles_is_rejected and ::test_an_empty_bubble_is_rejected assert the OLD contract (an AssertionError escaping LB.turn()), which TASK-156 deliberately replaced with a corrective-retry-then-holding-message contract. Update the tests to the new contract.
- Six tests fake luna_brain.Client but not the refusal classifier, so they now spawn a real 'claude -p' subprocess inside the offline suite (flagged as collateral risk in TASK-156's own notes): five in tests/test_wa_luna_campaign.py (decline ack, re-engagement, consent-no-tap, land-named-on-a-campaign-thread, the fresh_session test near line 315) and tests/test_wa_campaign_sender.py::test_status_shows_delivery_statuses_errors_and_replies. Inject the same fake transport seam tests/test_wa_luna_soft_answers.py already uses.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/wa/luna_brain.py passes the last outbound message's kind/question (what the bot last asked or offered on this thread) to app/wa/luna/refusal.is_unambiguous_refusal, short context only, with a stated and justified default for a first inbound with no prior outbound
- [x] #2 app/wa/luna/refusal.py's SYSTEM_PROMPT uses that context for exactly one distinction (refusing the conversation vs. answering the question asked); every failure mode still resolves to NOT a refusal
- [x] #3 Measured against the live classifier: a bare 'Nein' and 'Nein, danke' after the campaign opener come out as refusals; the same words after a gate question (Urkunde/Bayern/city/housing) do not -- verdicts reported, not assumed
- [x] #4 tests/test_wa_luna_brain.py::test_too_many_bubbles_is_rejected and ::test_an_empty_bubble_is_rejected assert the corrective-retry-then-holding-message contract instead of an escaping AssertionError
- [x] #5 The five tests_wa_luna_campaign.py tests and test_wa_campaign_sender.py::test_status_shows_delivery_statuses_errors_and_replies inject a fake refusal-classifier transport and no longer spawn a live 'claude -p' subprocess
- [x] #6 tests/test_wa_luna_soft_answers.py, tests/test_wa_luna_brain.py, tests/test_wa_luna_campaign.py and tests/test_wa_campaign_sender.py each pass standalone
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. app/wa/luna_brain.py: turn_context() gains last_outbound -- the single most recent outbound wa_messages row strictly before the inbound (id < inbound.id), as one _message_view, or None with no prior outbound. Not filtered by LAST_TURN_KEY (that filter is for what the *model* has not seen yet; the refusal classifier needs the literal last thing sent, whoever sent it).
2. app/wa/luna_brain.py:turn(), decline branch: read our_last_message = (context.get("last_outbound") or {}).get("text") and pass it to RF.is_unambiguous_refusal(text, our_last_message=our_last_message).
3. app/wa/luna/refusal.py: is_unambiguous_refusal gains a keyword-only our_last_message=None param; builds a JSON stdin payload {our_last_message, candidate_reply} instead of raw text (transport signature unchanged in shape -- still one string in, one string out, so existing fake transports need no change). SYSTEM_PROMPT rewritten to use our_last_message for exactly one distinction: a FACT question (a document/region/city/housing) vs an INTEREST/willingness/consent question (the campaign opener included) or anything else -- a short negative answers a FACT question, refuses everything else; our_last_message=null falls back to reading candidate_reply alone (unchanged prior behavior).
4. Measure live: bare Nein/Nein-danke after the campaign opener vs after each of the four named gate questions (Urkunde, Bayern, city, housing), plus the two already-correct shapes and two soft-answer controls, via direct RF.is_unambiguous_refusal() calls against the real claude CLI.
5. tests/test_wa_luna_brain.py: replace the two AssertionError-expecting tests (too-many-bubbles, empty-bubble) with the corrective-retry-then-holding-message contract (two tests each: retry succeeds, retry fails twice -> BLOCKED_REPLY_DE + escalated), mirroring tests/test_wa_luna_dialog_rules.py's own coverage of the same TASK-156 change.
6. tests/test_wa_luna_campaign.py and tests/test_wa_campaign_sender.py: inject monkeypatch.setattr(RF, "_live_transport", ...) in every test that drives the decline=true path with a faked luna_brain.Client but no fake for the classifier (9 tests found in test_wa_luna_campaign.py -- more than the 5 originally flagged by TASK-156's own risk note -- plus the 1 in test_wa_campaign_sender.py), so none of them spawn a live claude -p subprocess.
7. Run the four lane test files individually and together; report result lines and the measured verdicts.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified live (real claude -p, not a test): RF.is_unambiguous_refusal measured against the campaign opener text and against each named gate question. Results:
- 'Nein, kein Interesse' (no context) -> refusal True
- 'Nein danke, habe schon eine Stelle in Hessen' (no context) -> refusal True
- 'Nein, danke' after the campaign opener -> refusal True (was False before this fix)
- 'Nein' after the campaign opener -> refusal True (was False before this fix)
- 'Nein, danke' after 'Haben Sie die deutsche Urkunde schon?' -> NOT refusal (False)
- 'Nein' after 'Haben Sie die deutsche Urkunde schon?' -> NOT refusal (False)
- 'Nein' after 'Suchen Sie eine Stelle in Bayern?' -> NOT refusal (False)
- 'Nein' after 'Benoetigen Sie eine Unterkunft (Wohnung)?' -> NOT refusal (False)
- 'Nein' after a city gate question -> NOT refusal (False)
- 'Nein' with our_last_message=null (no prior outbound) -> NOT refusal (False, unchanged fallback)
- 'vielleicht, mal sehen' even with the campaign-opener context -> NOT refusal (False)
- 'erst naechstes Jahr' even after a gate question -> NOT refusal (False)
Every soft-answer/failure-mode case from tests/test_wa_luna_soft_answers.py's own phrase table still passes with the new context-aware SYSTEM_PROMPT and payload shape (30/30).

Lane tests: tests/test_wa_luna_soft_answers.py -> 30 passed. tests/test_wa_luna_brain.py -> 179 passed (2 old AssertionError-contract tests replaced with 4 corrective-retry-contract tests). tests/test_wa_luna_campaign.py -> 60 passed (9 tests now inject a fake refusal-classifier transport, up from the 5 named in TASK-156's own risk note -- 3 more decline=true tests found live-calling the classifier, plus the consent-no-tap test faked defensively though it never reaches the classifier). tests/test_wa_campaign_sender.py -> 51 passed (1 test faked). All four files together: 320 passed in ~22s (previously ~69s with live subprocess calls in the unfaked tests). pyflakes clean on both touched source files; two pre-existing, unrelated pyflakes findings in the test files (unused TestClient import in test_wa_luna_brain.py, unused local 't' in test_wa_luna_campaign.py) predate this change and were left alone.

Did not touch bridge/**, tools/wa_bridge.py, app/wa/bridge.py, .env, systemd units, or git. Files touched: app/wa/luna_brain.py, app/wa/luna/refusal.py, tests/test_wa_luna_brain.py, tests/test_wa_luna_campaign.py, tests/test_wa_campaign_sender.py.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed the missing input: app/wa/luna_brain.py:turn_context() now records last_outbound (the literal text of the single most recent outbound WhatsApp message, whoever sent it, or None with no prior outbound). turn()'s decline branch passes that text as our_last_message to app/wa/luna/refusal.is_unambiguous_refusal, which now sends the classifier a small JSON envelope {our_last_message, candidate_reply} instead of the reply alone. SYSTEM_PROMPT was rewritten to use our_last_message for exactly one distinction: was our last message asking for one FACT about the candidate (a document, a region, a city, housing) -- then a short negative answers it, not a refusal -- or was it about their INTEREST/willingness/consent to continue at all (the campaign opener included), or is our_last_message null -- then a short negative is read as it would have been before this fix (unsure -> not a refusal for null; an unambiguous refusal for the INTEREST/willingness case). Measured live: bare 'Nein'/'Nein, danke' after the campaign opener now come out True (were False); the same words after each of the four named gate questions (Urkunde, Bayern, a city, housing) stay False; every soft-answer and failure-mode case is unchanged. Also repaired: tests/test_wa_luna_brain.py's two tests asserting the old AssertionError-out-of-turn() contract now assert the TASK-156 corrective-retry-then-holding-message contract instead; and 9 tests across tests/test_wa_luna_campaign.py plus 1 in tests/test_wa_campaign_sender.py that drove the decline path with a faked brain client but no fake for the classifier now inject one, so the offline suite never spawns a live claude -p subprocess. Verified: all four lane files pass individually and together (320 passed, ~22s, down from ~69s with the live-subprocess tests).
<!-- SECTION:FINAL_SUMMARY:END -->
