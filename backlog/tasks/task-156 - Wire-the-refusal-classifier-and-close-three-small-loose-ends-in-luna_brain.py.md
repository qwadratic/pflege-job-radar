---
id: TASK-156
title: Wire the refusal classifier and close three small loose ends in luna_brain.py
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 09:35'
updated_date: '2026-09-22 09:35'
labels: []
dependencies: []
modified_files:
  - app/wa/luna_brain.py
  - app/wa/luna/grounding.py
  - app/wa/luna/refusal.py
  - tests/test_wa_luna_dialog_rules.py
  - tests/test_wa_luna_soft_answers.py
ordinal: 164000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Verification on 2026-09-22 found four small, independent loose ends in app/wa/luna_brain.py. (1) TASK-155's refusal classifier (app/wa/luna/refusal.py) was built, tested offline (26 tests) and measured against the real small model on a 41-case German corpus (39/41 agreement, zero false refusals), but was never connected: the decline branch still trusted the model's own decline flag outright, so a soft/deferred/maybe answer the model misread as decline=true ended the conversation for good. (2) F2: the bubble-count style check (_check, MAX_BUBBLES) ran OUTSIDE the try/except that protects a turn (luna_brain.py:1286) -- a 3-bubble reply raised AssertionError straight out of turn() and the candidate got nothing at all (hit 1 of 7 live turns the day this was found), breaking the corrective-retry-then-holding-message contract every other checked rule in this module already honours. (3) F1: luna_brain.py:1348-1350 overwrote card._escalate_reason unconditionally with the model's own escalate_reason, so a turn that was both flagged (the demoted exhaustive-claim check, TASK-154) and escalated lost the flagged sentence -- two different facts sharing one string field, only the last write survived. (4) F3: the offer turn's own required pool-branch wording using the verb 'vormerken' ("...oder darf ich Sie gleich fuer alle dort passenden Stellen vormerken?") tripped grounding.py's exhaustive-claim detector on every offer turn that phrased it this way, because _OFFER_SENTENCE_RE's verb list did not include it -- a flag firing on the happy path that teaches a human to ignore flags.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/wa/luna_brain.py:turn() calls app/wa/luna/refusal.is_unambiguous_refusal on the candidate's own text only on the decline path (out.get("decline") and not was_declined and not consent_no_tap), and only takes the decline branch (P.DECLINE_ACK_DE, card.declined) when the verdict is an unambiguous refusal; every other verdict (a genuine disagreement or any classifier failure mode) keeps the conversation alive and is recorded on the card (card._escalated/_escalate_reason) so a human sees it and a silent classifier outage is visible
- [x] #2 The 1-2 bubble/non-empty style check runs inside _checked_reply's own corrective-retry contract, so a style violation on the first pass is a corrective retry (and, on a second violation, the holding message plus an escalation) rather than an AssertionError raised out of turn()
- [x] #3 card._escalate_reason accumulates every distinct fact recorded on the same turn (a refused decline, a demoted exhaustive-claim flag, a two-strikes blocked reply, the model's own escalate_to_manager reason) instead of the last write silently overwriting an earlier one, without adding a new card field
- [x] #4 grounding.py's exhaustive-claim detector (_OFFER_SENTENCE_RE) does not flag the offer turn's own required pool-branch sentence when it uses 'vormerken', while a genuine exhaustive claim elsewhere in the same reply still flags
- [x] #5 tests/test_wa_luna_dialog_rules.py and tests/test_wa_luna_soft_answers.py each cover their items and pass standalone
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. app/wa/luna_brain.py: import app.wa.luna.refusal as RF. In turn(), compute decline_candidate = out.get("decline") and not was_declined and not consent_no_tap; when true, call RF.is_unambiguous_refusal(text) and only set decline_now=True on an unambiguous-refusal verdict, else record a note_escalation() call with the classifier's reason and let the turn fall through the existing elif chain (KEEP TALKING).
2. Add a local note_escalation(reason) closure in turn() that appends to card._escalate_reason (joined) instead of assigning, and set card._escalated=True; replace the three direct card["_escalated"]/card["_escalate_reason"] write sites (checked["escalate_reason"], checked["flagged"], out.get("escalate_to_manager")) to call it instead of assigning directly.
3. Move the _check() bubble-count/non-empty call from turn() into _checked_reply()'s own _run() closure, and widen the except clauses from GR.ReplyRejected to AssertionError (ReplyRejected already subclasses it) so a style violation on either pass shares the corrective-retry-then-holding-message contract.
4. grounding.py: add "vormerk" to _OFFER_SENTENCE_RE's alternation so the offer turn's own required pool-branch sentence using "vormerken" is excluded from the exhaustive-claim detector, scoped per-sentence so a genuine claim elsewhere in the same reply still flags. Update the module docstring with a short ROUND 6/F3 note.
5. tests/test_wa_luna_soft_answers.py: add a lightweight turn()-level fixture (luna/_out/fake_client, same pattern as tests/test_wa_luna_brain.py) and tests for a soft answer keeping the conversation alive + recording it, an unambiguous refusal still ending it, a classifier failure also keeping it alive + recorded, and the classifier not running on an ordinary turn.
6. tests/test_wa_luna_dialog_rules.py: add tests for a 3-bubble reply becoming a corrective retry (and, on persistent failure, the holding message plus escalation), a turn that is both flagged and model-escalated keeping both reasons, the offer's own 'vormerken' sentence not flagging, and a genuine exhaustive claim next to that sentence still flagging.
7. Run only tests/test_wa_luna_dialog_rules.py and tests/test_wa_luna_soft_answers.py; report the result lines.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified: tests/test_wa_luna_dialog_rules.py -> 167 passed in 2.46s (grew from 162 by 5: 2 for the bubble-count corrective-retry contract, 1 for flagged+escalated keeping both reasons, 2 for the vormerken offer-sentence exclusion + a genuine claim nearby still flagging). tests/test_wa_luna_soft_answers.py -> 30 passed in 0.49s (grew from 26 by 4: soft-answer keeps talking + is recorded, unambiguous refusal still ends it, a classifier transport failure also keeps talking + is recorded distinctly, the classifier is not called on an ordinary non-decline turn). pyflakes clean on all five touched files. Ran only the two lane files per Ivan's testing rule (lane tests only while building); no full-suite run from this session.

Collateral risk flagged for Verify: out-of-lane tests that already exercise the decline=true path with a fully faked luna_brain.Client but no fake for the new RF.is_unambiguous_refusal call -- tests/test_wa_luna_campaign.py (test_a_decline_sends_the_fixed_ack_once_marks_the_card_and_then_stays_silent, test_a_clear_re_engagement_after_a_decline_resumes_the_funnel, test_a_consent_no_tap_is_never_a_decline, test_a_decline_naming_another_land_on_a_campaign_thread_is_a_decline_not_the_region_question, and the fresh_session test near line 315) and tests/test_wa_campaign_sender.py::test_status_shows_delivery_statuses_errors_and_replies -- will now hit refusal.py's live default transport (a real 'claude -p' subprocess; C.LUNA_CLAUDE_BIN is on PATH and rides this host's own CLI auth per app/wa/config.py's own comment) and are not marked pytest.ini's 'llm' marker, unlike the persona tests that already expect a live CLI. Not fixed here: those files are outside this task's lane (luna_brain.py/grounding.py/refusal.py/the two named test files only); the fix there is a one-line monkeypatch of RF._live_transport (or the higher-level app.wa.luna.refusal.is_unambiguous_refusal) per file, the same seam tests/test_wa_luna_soft_answers.py's own new fixture already uses.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Wired app/wa/luna/refusal.py's classifier into the decline branch (runs only when out.get("decline") and not was_declined and not consent_no_tap; an unambiguous-refusal verdict is required to actually decline, every other verdict -- disagreement or any classifier failure mode -- keeps the conversation alive and is recorded on card._escalated/_escalate_reason). Moved the 1-2 bubble style check (_check) inside _checked_reply's own corrective-retry closure so a style violation shares the same corrective-retry-then-holding-message contract as every GR.check_reply guard, instead of raising straight out of turn() (F2). Added a note_escalation() closure in turn() so card._escalate_reason accumulates every distinct fact recorded on the same turn instead of the last write silently overwriting an earlier one (F1) -- no new card field. grounding.py: added "vormerk" to _OFFER_SENTENCE_RE so the offer turn's own required pool-branch sentence ("...oder darf ich Sie gleich fuer alle dort passenden Stellen vormerken?") no longer trips the exhaustive-claim detector, while a genuine claim elsewhere in the same reply still flags (F3); module docstring gained a ROUND 6 note. Verified: tests/test_wa_luna_dialog_rules.py -> 167 passed; tests/test_wa_luna_soft_answers.py -> 30 passed; pyflakes clean on all five touched files. Lane-scoped: did not touch bridge/**, .env, git, or any file outside app/wa/luna_brain.py, app/wa/luna/grounding.py, app/wa/luna/refusal.py, tests/test_wa_luna_dialog_rules.py, tests/test_wa_luna_soft_answers.py. Flagged a real collateral-risk finding for Verify (see Implementation Notes): the new classifier call has a live-CLI default, and a few out-of-lane, not-'llm'-marked tests that fake luna_brain.Client but not the refusal classifier will now reach that live default.
<!-- SECTION:FINAL_SUMMARY:END -->
