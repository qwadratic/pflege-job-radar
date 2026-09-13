---
id: TASK-80
title: Explicit button-confirmed consent for anonymized profile send
status: Done
assignee: []
created_date: '2026-09-13 11:14'
updated_date: '2026-09-13 11:40'
labels: []
dependencies: []
ordinal: 80000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Recon found the real system has an explicit-consent flow already (contradicting our assumption it was our own extension) -- and beyond the soft LLM-driven ask we already match, it has a SECOND, harder gate before named clinic submission: an interactive WhatsApp button ('Ja, ich bestätige') the candidate must tap, never inferred from free text. We don't do named submission (funnel stops at consent, by explicit decision), but Ivan wants that button-confirmed rigor applied to the one consent point we do have: replace model-inferred anonymous_send_consent with a real button tap, the same 'decided in code, not by the model' pattern already used for opt-out/reject/out-of-scope-region.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The turn where card_patch.anonymous_send_offered newly becomes true attaches real WhatsApp buttons (Ja/Nein, stable ids) to that outbound message
- [x] #2 anonymous_send_consent is set to True/False in code from the button tap (button_id), never trusted from the model's own card_patch for that field
- [x] #3 A free-text reply instead of a button tap does not silently grant consent -- the model can re-ask, but code still requires the button
- [x] #4 Existing consent-flow persona tests updated to tap the button rather than relying on free-text inference; new tests cover the button-only enforcement
- [x] #5 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/luna_brain.py: CONSENT_YES_ID/CONSENT_NO_ID/CONSENT_BUTTONS constants. turn() strips anonymous_send_consent from the model's own card_patch unconditionally (never trusted) and sets it ONLY from an actual button_id match against an already-offered card -- same 'decided in code, not by the model' pattern as opt-out/reject/out-of-scope-region. Buttons are attached exactly on the turn anonymous_send_offered newly flips true (just_offered), never re-attached on a later restatement. _user_payload() gained is_button_reply so the model can honestly tell a real tap from typed text that merely says yes, and prompts.py's new CONSENT IS A BUTTON TAP rule tells it to nudge toward the buttons rather than claim consent from free text. Updated tests/test_wa_luna_personas.py's _run() and tests/test_wa_luna_e2e_funnel.py's _run_persona() to simulate a real tap once buttons appear (a real WhatsApp UI renders them as taps, not text) instead of feeding more free text. Live-verified against the real claude CLI (not just fakes): the close-sequence persona test now correctly reaches consent via a tap, and a spot debug run confirmed the exact intended behavior -- typing 'passt für mich' instead of tapping gets a 'please tap one of the two buttons' nudge, not silent consent. 7 new unit tests (fake client) + offline suite green (1085 passed, same 5 pre-existing unrelated failures).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Anonymous-send consent is now a real, code-enforced WhatsApp button tap (Ja, gerne / Nein danke), never inferred from free text -- matching the rigor the real reference system already applies to its own harder, named-clinic-submission gate. The model can still ask naturally and react to what the candidate says, but the compliance-critical boolean is set exclusively by button_id in code. Caught and fixed a real gap while wiring this up: the initial implementation still let the model's own card_patch silently set consent from typed 'yes' since the strip-it-out step was only designed, not actually coded -- a dedicated test caught it immediately.
<!-- SECTION:FINAL_SUMMARY:END -->
