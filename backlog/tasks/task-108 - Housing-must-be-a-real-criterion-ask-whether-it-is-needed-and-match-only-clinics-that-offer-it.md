---
id: TASK-108
title: >-
  Housing must be a real criterion: ask whether it is needed and match only
  clinics that offer it
status: Done
assignee: []
created_date: '2026-09-16 14:40'
updated_date: '2026-09-16 17:28'
labels: []
dependencies: []
type: bug
ordinal: 108000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Checked 2026-09-16 on the live board: 483 of 3905 postings and 69 of 298 clinics carry enr_housing. app/data.py:filter_jobs supports housing="1" and the model tool search_postings exposes it, but luna_brain.market_snapshot (the harness-computed shortlist Luna is allowed to name) filters only by role, city and department, so a candidate who needs a flat can be offered clinics with no housing data. The housing gate only records housing_known/people_count -- it never asks whether housing is needed at all, and constitution housing_principle tells Luna to say "Most clinics offer a small apartment", which our own data does not support (12 percent of postings). Ivan 2026-09-16: this has to be taken into account.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 the card records whether the candidate needs housing (and for how many people); the gate asks it as a plain yes/no first (TASK-97 style) and only then the number of people, and a candidate who does not need housing is not asked for a headcount
- [x] #2 when housing is needed, market_snapshot builds the shortlist and matching_clinics_count from postings that offer housing; each shortlist entry says whether housing is offered, and the snapshot reports how many matches exist with and without housing so Luna can be honest when there are none in the wanted city
- [x] #3 the prompt and constitution stop claiming that most clinics provide a flat: housing may only be stated for a posting or clinic the board marks as offering it, otherwise Luna says the clinic confirms the terms
- [x] #4 the post-consent queue/matching path uses the same housing criterion as the shortlist
- [x] #5 offline tests cover needs-housing, no-housing-needed, housing wanted but none in the city, and the shortlist/queue agreement; llm persona runs show the yes/no housing question and an honest answer when no housing clinic matches; docs updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Card: housing_needed (model-written yes/no) next to housing_known/people_count; housing_known becomes harness-owned (CODE_OWNED_CARD_KEYS), set when housing_needed lands. luna_brain.housing_needed(card): explicit bool, else True when people_count is set (a headcount is only ever recorded for a flat), else None. Gate: needed False -> satisfied; needed True -> needs people_count; unknown -> legacy housing_known.
2. requirement_scoreboard: housing objective is the plain yes/no first (TASK-97 shape), the headcount only after a yes.
3. market_snapshot: with housing needed, shortlist + matching_clinics_count come from data.filter_jobs housing=1 rows; every shortlist entry carries housing: bool; new snapshot.housing block {needed, people_count, clinics_with_housing, clinics_any, city_regierungsbezirk, cities_with_housing[]} (alternatives from the board, same role/department filters, same Bezirk first).
4. app/data.py: one shared offers_housing(job) used by filter_jobs, the shortlist and the queue.
5. queue.card_to_candidate records needs_housing/people_count; build_queue_entry ranks only clinics with a board-marked housing posting when housing is needed.
6. prompts + constitution: housing_principle rewritten (yes/no first, then headcount; housing only asserted for board-marked postings, otherwise the clinic confirms the terms); 'most clinics offer a small apartment' removed; OUTPUT_SCHEMA/OUTPUT_INSTRUCTION carry housing_needed, not housing_known.
7. Tests: offline (gate yes/no, no-housing-needed, housing wanted but none in the city, entry housing flags, legacy card, shortlist-vs-queue agreement) + llm personas (housing yes/no question, honest answer with no housing clinic in the city); docs/whatsapp.md updated.

Review fixes 2026-09-16 (confirmed findings, applied by the fixer):
8. prompts: DOCUMENT ASK FIRST ASK and CLOSE SEQUENCE stop reading card.housing_known (true one turn before the gate closes, since turn() sets it from the yes/no) and read requirement_scoreboard.housing == satisfied, the one computed gate. The no-flat sentence in CLOSE SEQUENCE reads market_snapshot.housing.clinics_with_housing == 0, not 'empty shortlist' (an empty shortlist also means 'gate still open').
9. _housing_satisfied: a card carrying only housing_known (import_history/migrate_candidates shape: the old system recorded that housing was discussed, never the answer) no longer settles the gate -- the yes/no is asked once. Consequence: the campaign population is no longer matched with the housing filter off. housing.needed stays null for it, so 'never answered' is distinguishable from 'answered no'.
10. New card field housing_flexible (model-written yes/no): 'wanted a flat, accepts a clinic without one'. The HOUSING rule's no-flat follow-up now has a home for its answer; housing_needed stays true, so the handoff still reads 'needs a flat for N, accepts without'. market_snapshot and queue.build_queue_entry unfilter on it; OUTPUT_SCHEMA/OUTPUT_INSTRUCTION/prompts/docs carry it.
11. _housing_cities: city_regierungsbezirk comes from an unfiltered city lookup (the city's own postings, whatever role/department), because the filtered rows are empty in exactly the branch that fills cities_with_housing; the sort key no longer compares against a None Bezirk (own-Bezirk-first only when we know one).
12. Tests: gate open for a flag-only imported card (shortlist/queue unfiltered no more), no close-ready payload while the headcount is missing, housing_flexible through market_snapshot + queue, alternative-city order with a null-Bezirk row.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (offline suite green: 1584 passed, 126 skipped).

Card/gate (app/wa/luna_brain.py): new housing_needed(card) -- explicit card.housing_needed bool, else True when a people_count is on the card (a headcount is only ever asked about a flat; that is what an imported card carries), else None. _housing_satisfied: a No settles the gate alone, a Yes needs the headcount, an older card with housing_known alone stays settled (never re-ask an answered question). housing_known is now code-owned (CODE_OWNED_CARD_KEYS) and set in turn() from the answer, so a model that writes only the flag can no longer close the gate without the fact the shortlist filters on; OUTPUT_SCHEMA/OUTPUT_INSTRUCTION carry housing_needed instead. next_objective: plain yes/no first ('brauchen Sie eine Unterkunft?'), _HOUSING_HEADCOUNT_OBJECTIVE only after a yes.

Shortlist: with housing needed, shortlist + matching_clinics_count come from the housing-marked postings only; every entry carries its own housing flag; new market_snapshot.housing = {needed, people_count, clinics_with_housing, clinics_ignoring_housing, city_regierungsbezirk, cities_with_housing[]}. cities_with_housing is filled only when the filtered search found no flat at all (same role/department filters, candidate's own Regierungsbezirk first, <=5) -- board rows only, so an alternative city is never invented. One criterion for everyone: new app/data.py:offers_housing (enr_housing), used by filter_jobs(housing=1), the shortlist and the queue.

Handoff (app/wa/queue.py): card_to_candidate records needs_housing/people_count; build_queue_entry ranks a candidate who needs a flat only against housing-marked postings and their clinics. Offline test asserts shortlist and queue name the same clinics.

Prompt/constitution: housing_principle rewritten (yes/no then headcount; a flat only for a board-marked posting, otherwise the clinic confirms the terms; 'Most clinics offer a small apartment' and the goal's blanket 'with housing support' removed; 'never say most/many/usually'). HOUSING rule also fixes the no-flat follow-up: one plain yes/no, never one named city and 'without a flat' joined by oder. migrate_candidates accepts housing_needed; VENDORED.md and docs/whatsapp.md (new 'Housing gate and criterion' block, close sequence, post-consent queue, import facts table) updated. import_history's facts contract was left as it is: the old system records only that housing was clarified plus alone/family, so a housing_needed column there would have nothing to read -- people_count covers the imported cards.

Live llm runs 2026-09-16 (claude-sonnet-5, one at a time): housing gate 2/2 ('Brauchen Sie für den Start dort auch eine Wohnung?' -> after the Ja 'Für wie viele Personen würde die Wohnung sein?'); no-flat city (Würzburg) 7 runs, all honest about the missing flat, but 2 of the first 3 bundled both ways out into one either/or question -- HOUSING now names that mistake (described, not quoted: the frozen prompt must carry no either/or example question) and the last 4 runs asked one plain yes/no; the persona test asserts it. Regression llm runs: close sequence 2/2, city/housing gate shape 1/1, family headcount 1/1, campaign full funnel 1/1 and flexible funnel 1/1 (their scripted housing answer is now two steps).

Decision to flag for review: an older/imported card that carries only housing_known (the earlier contact answered the housing topic, the answer itself is not recorded) keeps the gate settled and is NOT filtered by housing -- the snapshot reports needed: null. Re-asking those candidates instead is a one-line change if Ivan prefers it.

Review fixes applied 2026-09-16 (fixer, confirmed findings):

1. IMPORTED CARDS ARE ASKED THE HOUSING QUESTION ONCE (the open decision I had flagged, now decided the way the ACs read). _housing_satisfied no longer falls back to housing_known: a card carrying only that flag (what import_history/migrate_candidates produce -- the old system recorded that housing was discussed, never the answer) leaves the gate OPEN. Before, the gate closed on an answer that never existed and both consumers ran unfiltered (housing_needed None), i.e. the pre-TASK-108 bug survived for exactly the population TASK-98..107 campaigns target. It is not a re-ask: the old system asked its own coarser question and kept no answer we can read. market_snapshot.housing.needed null = never answered (and there is no shortlist then), false = answered no. A people_count on an imported card still settles it (a headcount is only ever asked about a flat). Test: test_an_imported_card_with_only_the_flag_is_asked_the_housing_question_once (brain) + test_an_imported_card_with_only_the_housing_flag_is_not_matched_as_if_it_answered (queue).

2. PROMPT RULES READ THE COMPUTED GATE, NOT THE FLAG. turn() sets housing_known as soon as the yes/no lands -- one step before the gate closes -- while DOCUMENT ASK FIRST ASK and CLOSE SEQUENCE were still keyed on card.housing_known, so between the Ja and the headcount the model was told 'settled' with an empty shortlist. Both now read requirement_scoreboard.housing; the CLOSE SEQUENCE no-flat sentence reads market_snapshot.housing.clinics_with_housing = 0 instead of 'an empty shortlist' (an empty shortlist also means 'a gate is still open' -- the TASK-82 mismatch class). Test: test_a_yes_without_the_headcount_never_reads_as_close_ready.

3. 'WANTED A FLAT, ACCEPTS ONE WITHOUT' HAS ITS OWN FIELD. New model-written card_patch.housing_flexible (OUTPUT_SCHEMA, OUTPUT_INSTRUCTION, HOUSING rule, constitution.housing_principle.matching, docs). The HOUSING rule's no-flat follow-up had no home for its answer, so the only representable Ja was flipping housing_needed to false -- which told the human handoff a family of two needs no flat. Now market_snapshot (housing.flexible/filtered) and queue.build_queue_entry drop the housing filter on that flag while needs_housing/people_count keep saying what was asked for. Test: test_wanting_a_flat_and_accepting_one_without_is_recorded_without_unsaying_the_need (brain) + test_a_candidate_who_accepts_a_clinic_without_a_flat_is_ranked_wider_and_still_reads_as_needing_one (queue, shortlist and queue still name the same clinics).

4. ALTERNATIVE CITIES: OWN BEZIRK FIRST ONLY WHEN WE KNOW IT. city_regierungsbezirk is read from the city's OWN postings (B.jobs_for({city}), unfiltered by role/department) -- the filtered rows are empty in exactly the branch that fills cities_with_housing -- and the sort key no longer compares against None, so a board row with no regierungsbezirk cannot lead the list the model reads top-down. Test: test_an_alternative_city_without_a_regierungsbezirk_does_not_lead_the_list (mutation-checked against the old lookup/sort).

Fallout in existing tests: cards that used housing_known=True as 'housing settled' shorthand now say housing_needed=False (tests/test_wa_luna_brain.py 17 cards, reporting 4, media_intake 2, import_history 2). docs/whatsapp.md housing section, import facts row and the close-sequence wording updated.
Not re-run: the llm personas (housing gate, Wuerzburg no-flat). The no-flat persona now has one more assertable step (the Ja recorded as housing_flexible, not as housing_needed false) -- worth one live run before the next campaign.
Offline suite: 1600 passed, 126 skipped, 68 deselected.

Final offline suite after the review fixes: 1607 passed, 126 skipped, 68 deselected (2:23) -- 7 tests more than before (4 new brain/queue housing tests, 2 new test-thread tests, 1 queue import-shape test).

Final verification 2026-09-16 (review + adversarial verify + fixer, 6 findings fixed): offline suite 1600 passed, all 10 acceptance criteria evidenced, purge e2e 29/29 checks. Two further defects found by the final verifier and fixed by Claude afterwards: the session transcript is now unlinked inside the wipe transaction (a crash after the commit used to leave the whole conversation on disk with nothing naming it), and webhook events stored with phone NULL whose raw payload carries the test number are wiped too (reported separately in the dry run). Both mutation-checked. Offline suite after the fixes: 1609 passed. Deployed: pflege-wa.service restarted 17:27 UTC, health webhook_ready/outbound_ready/luna_ready/stt_ready true.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Housing is now a real criterion: the gate asks a plain yes/no first and the headcount only after a yes, the shortlist and matching count are built from postings the board marks as offering housing (one shared predicate with the post-consent queue), each shortlist entry carries its housing flag, and when no clinic in the wanted city offers a flat the snapshot lists board-backed alternative cities so Luna can say so honestly. The 'most clinics offer a small apartment' claim is gone from the prompt and constitution. Verified by offline tests, llm persona runs (housing yes/no 2/2, no-flat city honest answer, close sequence and campaign funnels) and the full offline suite.
<!-- SECTION:FINAL_SUMMARY:END -->
