---
id: TASK-155
title: >-
  Soft-answer refusal classifier: keep the conversation alive unless a small
  model flags an unambiguous decline
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 08:32'
updated_date: '2026-09-22 08:37'
labels: []
dependencies: []
ordinal: 163000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan's product rule (2026-09-22): a conversation ends only on an unambiguous refusal to continue -- a qualified yes, a maybe, a deferral, or a question back must keep it alive (acknowledge the reservation, give one real fact from the board, ask one question that moves the card forward). The DECLINE section in app/wa/luna/prompts.py currently has no rule at all for soft/deferred/conditional answers, only for a flat no and for a Nein answering a gate question. Ivan's explicit instruction on HOW: do not hand-write a German phrase/regex/keyword list to detect a refusal -- language is unpredictable (the exhaustive-claim rule in this repo took four rounds of surface-pattern fixes and the live model still broke pattern three). Ask a small model (Haiku tier) one narrow question -- is this an unambiguous refusal to continue -- and act on the answer. Cost shape: the classifier runs only when the model tries to end the conversation, not on every inbound turn. Asymmetry that sets the safe default: a wrongly-ended conversation is a lost, silently-unnoticed candidate; a wrongly-continued one costs one more polite message -- so every failure mode (timeout, missing binary, unparseable/ambiguous verdict) means KEEP TALKING, recorded loudly rather than silently changing behavior.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/wa/luna/refusal.py exposes a function that takes the candidate's own text and returns an unambiguous-refusal verdict via a small-model call through the same claude-CLI mechanism luna_brain.py already uses, with its own model constant, its own short timeout, and an injectable transport so tests need no live model
- [x] #2 Every classifier failure mode -- timeout, missing binary, unparseable output, ambiguous verdict -- resolves to NOT a refusal (keep talking), with the reason recorded rather than silently swallowed
- [x] #3 refusal.py is not wired into luna_brain.py in this task (another workflow owns that file); its docstring states the exact call site that will use it and that it runs only there
- [x] #4 app/wa/luna/prompts.py DECLINE section is rewritten so the model ends a conversation only on an unambiguous refusal, and states in German what to do for soft/deferred/conditional answers: acknowledge the reservation in one clause, give one real fact the market snapshot supports (never an invented figure), ask one question that moves the card forward -- keeping the existing one-question-per-message discipline and voice
- [x] #5 app/wa/config.py adds the classifier's model and timeout settings, validated loudly on bad input like the neighboring settings
- [x] #6 tests/test_wa_luna_soft_answers.py covers, with a fake transport: classifier verdict maps to the right decision, every failure mode lands on KEEP TALKING, and a small table of German soft-answer inputs documents expected behavior; no assertion depends on a live model call
- [x] #7 tests/test_wa_luna_soft_answers.py passes standalone
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. app/wa/luna/refusal.py: new module, Verdict(is_refusal, reason), is_unambiguous_refusal(text, transport=None) calling a stateless one-shot 'claude -p' through C.LUNA_CLAUDE_BIN/C.REFUSAL_MODEL/C.REFUSAL_TIMEOUT_SEC; every failure path (missing binary, timeout, non-zero exit, bad JSON, non-bool verdict) caught and mapped to Verdict(False, reason), logged at ERROR; docstring names the luna_brain.py turn() decline branch as the only intended call site, not wired there.
2. app/wa/config.py: REFUSAL_MODEL (default claude-haiku-4-5, raises if explicitly blanked) and REFUSAL_TIMEOUT_SEC (default 20, raises on non-integer or <=0); readiness() reports refusal_model under BRAIN=luna.
3. app/wa/luna/prompts.py DECLINE section: split into the unambiguous-refusal definition, a new NOT A DECLINE KEEP GOING rule (acknowledge in one clause, one real market-snapshot fact, one forward question) covering the soft/deferred/conditional shapes, and the unchanged declined-thread consequence rule.
4. tests/test_wa_luna_soft_answers.py: fake-transport tests for the verdict mapping, every failure mode, markdown-fence/prose tolerance, and a parametrized German phrase table (7 soft shapes + bare nein + 2 hard refusals + one buried-refusal sentence) documenting expected classification -- no live model, no subprocess.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified: import app.wa.config/app.wa.luna.prompts/app.wa.luna.refusal succeeds; REFUSAL_MODEL=claude-haiku-4-5, REFUSAL_TIMEOUT_SEC=20 by default; WA_REFUSAL_TIMEOUT_SEC=abc / =0 and WA_REFUSAL_MODEL='' each raise RuntimeError at import (checked live via subprocess). tests/test_wa_luna_soft_answers.py: 26 passed, 0 failed, no live claude CLI invoked (fake transports only). git diff confirms only app/wa/config.py, app/wa/luna/prompts.py, app/wa/luna/refusal.py (new), tests/test_wa_luna_soft_answers.py (new) touched -- luna_brain.py, grounding.py, tests/test_wa_luna_dialog_rules.py and bridge/** left untouched (owned by other concurrent workflows).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added app/wa/luna/refusal.py: a small-model (Haiku-tier) unambiguous-refusal classifier, called through the same claude-CLI subprocess mechanism app/wa/luna_brain.Client already uses, stateless and one-shot, with an injectable transport and every failure mode (timeout, missing binary, unparseable/ambiguous output) mapped to NOT-a-refusal and logged loudly -- not wired into luna_brain.py (owned by another workflow), its docstring names the exact turn() decline branch it is built for. app/wa/config.py gains REFUSAL_MODEL/REFUSAL_TIMEOUT_SEC, both validated loudly at import. app/wa/luna/prompts.py's DECLINE section now only lets the model flag decline=true on an unambiguous refusal and adds a NOT A DECLINE, KEEP GOING rule (acknowledge the reservation, one real market-snapshot fact, one forward question) for maybe/deferred/conditional/question-back answers. tests/test_wa_luna_soft_answers.py: 26 tests, all offline (fake transports), covering the verdict mapping, every failure mode, and a documented German phrase table; run standalone: 26 passed.
<!-- SECTION:FINAL_SUMMARY:END -->
