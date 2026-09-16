---
id: TASK-97
title: Qualification question must not be an either/or that a plain "ja" answers
status: Done
assignee: []
created_date: '2026-09-14 09:44'
updated_date: '2026-09-14 13:28'
labels: []
dependencies: []
type: bug
ordinal: 97000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in Ivan manual test 2026-09-13 (the manual test number): Luna asked "Haben Sie schon eine deutsche Pflege-Urkunde, oder sind Sie noch im Anerkennungsverfahren (Defizitbescheid/Kenntnisprüfung)?"; the candidate answered "ja", the first re-ask was again either/or and got "ja" again, only the third ask (plain yes/no) resolved it -- one avoidable round-trip. THINK_ORDER rule 4 tells the model to read Ja/Ok as yes, which is meaningless after an either/or question, and _OBJECTIVE_ORDER labels the gate "Urkunde/Defizitbescheid/Kenntnisprüfung", inviting the three-way question.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 the prompt tells Luna to ask the qualification question as a plain yes/no first (Urkunde already in hand?) and only after a "nein" ask about Defizitbescheid/Kenntnisprüfung; no either/or question that a "ja" could answer, anywhere
- [x] #2 a "ja"/"ok" answer to an either/or question is treated as ambiguous and the very next re-ask is a strict yes/no, never another compound question
- [x] #3 the qualification next_objective label no longer lists the three options as one question
- [x] #4 offline prompt tests pass; an llm-marked persona test (real claude CLI) where the candidate answers "ja" to the qualification question resolves the qualification path within one re-ask
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. prompts.py THINK_ORDER 4: soft Ja/Ok = yes only after a real yes/no question; after an either/or a bare Ja is ambiguous, record nothing, very next re-ask is a strict yes/no, never another compound question.
2. prompts.py RULES: new YES/NO QUESTIONS rule (no X-oder-Y question a bare Ja could answer, any gate); QUALIFICATION rule gets the ask order (plain yes/no 'Urkunde in hand?' first, only after nein the recognition step, one yes/no at a time); CHAT OVER CARD + GUESS FREELY reworded so they do not contradict (bare Ja to an either/or = genuinely ambiguous).
3. constitution.json (injected into the same system prompt): live_market 'After they confirm Urkunde/Defizit/Prüfung with Ja/Ok/Passt: do NOT re-ask which of the three' and chat_first_confirmations contradict the new rule; primary_candidate_first.example_good and region.ask are either/or questions a Ja answers -> minimal rewording.
4. luna_brain.py _OBJECTIVE_ORDER qualification label only: plain yes/no Urkunde first, recognition step only on no.
5. Offline tests (tests/test_wa_luna_brain.py): system prompt carries the rule, constitution has no either/or examples, next_objective qualification label.
6. llm persona (tests/test_wa_luna_personas.py): candidate answers bare 'ja' to every qualification question; qualification_path=urkunde within one re-ask, set only after a Ja to a plain yes/no, at most one either/or qualification ask answered by Ja. Run live >= 2x.
7. Full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (prompt-only; no code gate):
- prompts.py THINK_ORDER 4: soft Ja/Ok = yes only after a yes/no question; after an either/or a bare Ja is ambiguous, record nothing, very next re-ask strict yes/no, never another compound question.
- prompts.py RULES: new YES/NO QUESTIONS rule (no 'oder'-joined options a bare Ja could answer, any gate); QUALIFICATION gets the ask order: Urkunde in hand? -> on Nein Defizitbescheid received? -> on Nein Kenntnisprüfung passed?, one yes/no per turn, skip steps the thread answers; CHAT OVER CARD + GUESS FREELY say a bare Ja to an either/or closes nothing / is always ambiguous.
- constitution.json (injected verbatim into the same system prompt) contradicted the rule: live_market 'After they confirm Urkunde/Defizit/Prüfung with Ja/Ok/Passt: do NOT re-ask which of the three' reworded to 'once the path is settled (Ja to a plain yes/no settles it, Ja to an either/or does not)'; chat_first_confirmations scoped to yes/no asks; example_good and region.ask were either/or questions, now plain yes/no.
- luna_brain.py _OBJECTIVE_ORDER qualification label: plain yes/no Urkunde first, recognition step only on no, one yes/no at a time.
Tests: tests/test_wa_luna_brain.py +4 offline (label, THINK_ORDER 4 + CHAT OVER CARD/GUESS FREELY, YES/NO + QUALIFICATION order, frozen system prompt carries no 'oder ...?' example question). tests/test_wa_luna_personas.py +2 llm: Olena answers every qualification question with bare 'ja' (natural flow) and a seeded variant whose first turn forces Ivan's either/or question via an extra per-call system line (not stored in the session), asserting the Ja settles nothing and the next re-ask is plain yes/no.
Live: baseline on the old prompt FAILED (either/or asked, bare ja recorded as urkunde). New prompt: natural test 3/3 passed (plain 'Haben Sie bereits die deutsche Pflege-Urkunde ...?' first), seeded test 2/2 passed ('Um sicherzugehen: Haben Sie bereits die deutsche Pflege-Urkunde ...?' then urkunde). anna/mai/maria personas re-run: passed.
Offline suite: 1191 passed, 126 skipped, 18 deselected.

Review fix 2026-09-14 (fixer), either-or-city-question-still-asked:
- luna_brain._OBJECTIVE_ORDER region/city/housing labels reworded: region is a plain yes/no on Bayern; city is an open 'which city' with no yes/no frame around a list of cities; housing is an open 'how many people would live in the flat'.
- constitution: live_market asks ONE open question (no list frame, no city against department); housing_principle.ask is one open question (never alone against family).
- prompts YES/NO QUESTIONS now also covers yes/no frames around options: Gibt es / Haben Sie / Ziehen Sie + a list joined by oder, city against department, alone against family.
Tests: offline label/constitution/rule test. New llm test tests/test_wa_luna_personas.py::test_olena_city_and_housing_questions_are_not_yes_no_frames_around_options (city gate + housing gate): 3 live runs, 6/6 turns asked open questions ('In welcher Stadt in Bayern möchten Sie arbeiten?', 'Wie viele Personen würden ... in die Wohnung ziehen?'). olena natural re-run passed.
Offline suite: 1270 passed, 126 skipped, 26 deselected.

Repair round 1 (2026-09-14): verifier found no TASK-97 defect (olena natural 2/2, seeded 2/2, city/housing 4/4 live). No TASK-97 code or prompt change in this round.

Repair round 2 (2026-09-14): no prompt change. tests/test_wa_luna_personas.py:_yes_no_frames_around_options false positive found in a live gate run: 'Super, danke für die Bestätigung – mit deutscher Urkunde 🎉' + 'Aktuell haben wir 6 offene Stellen in ganz Bayern – in welcher Stadt oder Region würden Sie denn gerne arbeiten?' was flagged (bubbles were joined, so the question ran into the unpunctuated first bubble; the W-word check only looked at the sentence start). The helper now splits per bubble and checks the clause holding 'oder' (split on , ; : – —). Offline check against the three historical yes/no frames from the TASK-97 review, Ivan's either/or qualification question and 'Wo möchten Sie arbeiten, in München oder Augsburg?': still flagged; the open W-questions above: not flagged. Live after the fix: city/housing gates 2/2, olena natural 1/1, seeded 1/1. The natural Olena test needing no re-ask is the prompt working (plain yes/no asked first); the ambiguous-Ja path is covered by the seeded test by design.

Validation 2026-09-14: baseline on the old prompt reproduced the bug live (either/or question, bare ja taken as urkunde). New prompt: natural bare-ja persona 3/3 + 2/2 (plain yes/no asked first), seeded either/or persona 2/2 + 2/2 (next re-ask strict yes/no), city/housing yes/no-frame test 4/4; existing personas anna/mai/maria pass. Region/city/housing labels also reworded to open or plain yes/no questions. Remaining observation from final verifier: 'Gibt es eine Stadt in Bayern, die für Sie infrage kommt?' still appeared once (a yes/no frame without 'oder'); the test helper only flags 'oder' frames. Full offline suite 1281 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Luna now asks qualification as plain yes/no steps (Urkunde in hand? then, only on no, the recognition step), treats a bare Ja to any either/or question as ambiguous with a strict yes/no re-ask, and gate labels no longer invite either/or questions. Verified live against the real model (old prompt reproduced the bug; new prompt passed natural and seeded bare-ja personas repeatedly) and by the full offline suite (1281 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
