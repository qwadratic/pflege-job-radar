---
id: TASK-151
title: COUNT rule misreads German thousands dots and rejects honest approximations
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 00:31'
updated_date: '2026-09-22 00:32'
labels: []
dependencies: []
ordinal: 159000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reproduced live on the real board 2026-09-22, twice, with different outcomes (flaky). A candidate asked about Augsburg Intensivpflege; Luna's honest opener said "bayernweit ueber 2.300 Stellen" (true live figure 2462). grounding.check_reply's COUNT rule (the _COUNT_CLAIM_RE / _false_counts path) rejected it, reporting it saw [300] -- the digits-only number pattern breaks at the German thousands dot, so "2.300" is read as "300". Separately, even a correctly-parsed approximation ("ueber 2300", "rund 2500") was rejected outright because the rule demanded an exact match against an evidence number, and a truthful rounded German figure is ordinary language, not invention. Run A hit the parsing bug and recovered on the corrective retry; run B hit a second violation and the candidate got the holding message ("Eine Kollegin schaut sich Ihre Frage an") -- same question, same board, different luck, which is worse than a hard failure for the acceptance test Ivan's business partner runs in the next days. The rule's invention-detection job is real (the audit proved the model would otherwise write "Es gibt nur diese 5 Kliniken" against a true 224) and must not be weakened, only its number reading and its idea of "supported" corrected.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A German thousands dot/thin-space/non-breaking-space grouping ("2.300") parses as 2300, not 300, and a decimal comma ("2,5") is never read as a thousands group
- [x] #2 An approximation marker (ueber/mehr als/gut, knapp/fast, rund/etwa/ca.) changes what evidence supports: at-least, just-under, or within-tolerance of a real evidence number respectively -- a bare exact figure still has to match evidence exactly
- [x] #3 The audit counterexample ("nur diese 5 Kliniken" against a true 224) is still rejected, and an approximation marker cannot launder a figure with no evidence anywhere near it
- [x] #4 The COUNT rejection message states the parsed value and the literal text it was read off, not just the number
- [x] #5 tests/test_wa_luna_dialog_rules.py covers the German-dot parse, the decimal-comma non-confusion, each approximation kind (pass and fail), and the message content; offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce deterministically via app.wa.luna.grounding.check_reply directly (the parsing/threshold bug is in check_reply's own regex and comparison, not in the model call) using the reported sentence and true count 2462; confirm the rejection message matches what was observed live.
2. Add _de_number/_DE_NUM_RE to read German thousands dots and thin/non-breaking spaces, and to treat a comma as a decimal that is never folded into the thousands.
3. Add an approximation-marker regex and _approx_supported/_round_tolerance: ueber/mehr als/gut require some evidence number >= the stated figure; knapp/fast require an evidence number just under it (within the figure's own rounding tolerance); rund/etwa/ca. require an evidence number within that same tolerance on either side. The tolerance is half the rounding unit implied by the stated figure's own trailing zeros -- not a picked constant.
4. Rewrite the COUNT rejection message to show the parsed value and the literal text/phrase it came from.
5. Add tests: German-dot parse, bare-exact unchanged, ueber pass/fail, rund pass/fail (pinned), knapp/fast pass/fail, decimal-comma non-confusion, laundering-resistance, message-content, plus re-verify the existing audit-D and over-4000 tests still reject.
6. Run the full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Reproduced deterministically via GR.check_reply directly (bypassing the LLM/board round-trip, which is not needed since the bug is in check_reply's own regex and comparison): 'bayernweit ueber 2.300 Stellen' against counts={2462} raised exactly the reported message, parsing '2.300' as 300. Fix: _DE_NUM_RE/_de_number read German thousands dots and thin/non-breaking spaces and never fold a decimal comma into them; _APPROX_KIND/_approx_supported/_round_tolerance give ueber/mehr als/gut an at-least reading, knapp/fast a just-under reading, and rund/etwa/ca. a within-tolerance reading, where the tolerance is half the rounding unit the figure's own trailing zeros imply (the mathematical meaning of 'rounded to the nearest N'), so the marker can never launder a figure with no real evidence nearby. Pinned decision: rund 2.500 against a true 2462 is accepted (38 within the 50 tolerance implied by 2.500's two trailing zeros); rund 2.500 against 2000 is rejected. Rejection message now names the parsed value and the literal text/phrase it came from.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna/grounding.py: fixed the COUNT rule (check_reply) to read German-formatted numbers ('2.300' -> 2300, never 300) and to accept honest approximations (ueber/mehr als/gut, knapp/fast, rund/etwa/ca.) against real evidence instead of demanding an exact match. Invention detection is unchanged and unweakened: the audit counterexample ('nur diese 5 Kliniken' against a true 224) and the 'ueber 4000' fabrication test still reject, and a new test asserts a marker cannot launder a figure with no evidence nearby. Verified: tests/test_wa_luna_dialog_rules.py grew by 8 tests (128 -> 136, all passing) covering the German-dot parse, decimal-comma non-confusion, each approximation kind pass/fail, the pinned rund-2.500 decision, and the rejection message content. Full offline suite: 2307 passed, 127 skipped, 70 deselected, 5 warnings in 169.77s (up from the 2299-passing baseline by exactly the 8 new tests).
<!-- SECTION:FINAL_SUMMARY:END -->
