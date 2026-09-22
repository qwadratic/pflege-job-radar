---
id: TASK-144
title: >-
  Volume control, no-invention grounding and funnel-stage continuity in the Luna
  dialog
status: Done
assignee: []
created_date: '2026-09-21 10:21'
updated_date: '2026-09-21 10:53'
labels: []
dependencies: []
priority: high
type: feature
ordinal: 152000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-21, from the first real phone-rail conversation: three dialog rules the prompt alone cannot guarantee.

VOLUME. A candidate must never get a wall of vacancies. At most 5 positions in one message, each a short description (clinic, city, department, the one or two facts that matter), never a board URL or job link (the existing prompt ban stays), then how many more matched, then -- in the same turn -- a two-branch offer: narrow the search, with the concrete criteria that would narrow it for THIS candidate read off the actual result set, or be put forward to every matching clinic (the general pool). The cap belongs where the result set is assembled, so no prompt edit can raise it.

NO INVENTION. The model may state only what the board data contains; anything else is named unknown, never guessed. The prompt says so today but nothing checks it, so a clinic the tools never returned can still reach a candidate.

FUNNEL CONTINUITY. The card must carry the stage the candidate is in and the next turn must resume from it instead of restarting. Derive the stage from the gates requirement_scoreboard already computes -- there must not be a second state machine.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 market_snapshot assembles the offer: at most 5 named positions however large the matching set, an accurate count of how many more matched, and suggested narrowing criteria taken from the actual result set
- [x] #2 The two branches (narrow / pool) are both offered in the same turn and both recordable on the card, and the pool choice is persisted
- [x] #3 A reply naming a clinic or posting that neither the harness shortlist nor this turn's own tool calls returned is rejected loudly, and nothing is sent
- [x] #4 A reply naming more than 5 positions is rejected loudly
- [x] #5 The card carries the funnel stage (contact, qualification, matching, cv, documents, consent, submitted) with the timestamp it was entered, and the next turn resumes from it
- [x] #6 Offline suite green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. offer.py: build_offer assembles positions (<=5, one per clinic), remainder counts, narrowing criteria read off the result set, the two branch ids. No source_url ever leaves it.
2. luna_brain.market_snapshot: shortlist becomes offer.positions -- one assembly, one cap. CLOSE_LIMIT is now OF.OFFER_LIMIT.
3. grounding.py: read tools_server's call log from this turn's start offset, replay the calls through tools_server's own filter builders, and check the outgoing bubbles against that evidence plus the offer plus the names this thread already grounded. Two failures: NO INVENTION and VOLUME, both AssertionError out of turn().
4. luna_brain.funnel_stage: name the first gate requirement_scoreboard finds open; turn() persists card.stage/stage_at; the scoreboard carries stage/stage_since so the model resumes from it.
5. prompts.py: VOLUME, NO INVENTION and FUNNEL CONTINUITY rules, match_branch in the output contract.
6. tests/test_wa_luna_dialog_rules.py + the full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Decisions worth keeping:

- The evidence a reply is checked against is per THREAD, not strictly per turn. A model answering a follow-up about a clinic it named two turns ago (out of its own session memory) would otherwise fail a true sentence and stall the thread, and catch-up would retry into the same wall. card._grounded_clinics remembers only names that were really in evidence, so a name that was never in any tool result is still rejected -- which is the invention the rule is for.

- The replay deliberately does NOT apply a tool's display limit. How many rows a listing shows the model is presentation (tools_server.LISTING_LIMIT, TASK-145); the grounding question is which clinics the query was about. Naming too many of them is a volume problem, and check_reply catches that on its own. This also keeps the replay working when the tool surface changes shape -- which it did mid-task (search_postings went from a list to {shown,total}).

- The clinic detector is two detectors: every board clinic name that appears in the text, plus a strong institutional head word (Klinikum, Krankenhaus, ...) followed by name words. The generic 'Klinik' is excluded and a name stops at the first word that is never part of one, so 'die Klinik Ihrer Wahl' and 'im Krankenhaus Vollzeit arbeiten' are not mistaken for houses. A false rejection blocks a live reply, so the detector errs toward the board's own vocabulary.

- The stage is not a second state machine: funnel_stage(board) names the first gate requirement_scoreboard already found open. luna_brain.SCOREBOARD_GATES was added so reporting.py reads the gate list instead of a hard-coded skip list (its old one went stale the moment the scoreboard grew two hint keys).

- Cross-lane, reported to the coordinator: tools_server's new match_cv_to_postings (TASK-145) asks luna_brain to pass WA_LUNA_PHONE and to allowlist the tool. Both are in this lane, so they were wired here: Client.phone is a plain attribute set by turn(), not a constructor argument, because six test modules stand a client up as LB.Client() with no arguments.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ivan's three dialog rules are now enforced in code, not only in the prompt.

VOLUME: app/wa/luna/offer.py:build_offer assembles what a candidate may be shown -- at most OFFER_LIMIT=5 positions (one per distinct clinic), the real remainder count, and the criteria that actually discriminate in that result set, each value counted off the rows themselves and truncation reported as more_values. No source_url reaches the payload, so the link ban is structural. market_snapshot's shortlist IS offer.positions, so the list named and the number said left over cannot be counted from different rows. The outgoing text is checked too (grounding.check_reply), because the model can name clinics a tool returned rather than the ones we assembled.

NO INVENTION: app/wa/luna/grounding.py reads tools_server's call log from this turn's own start offset, replays those calls through tools_server's own filter builders and path handlers, and refuses any bubble naming a clinic that is in neither that evidence, the harness offer, nor this thread's earlier evidence. Two detectors find the mention: every board clinic name in the text, and a strong institutional head word followed by name words (so an invented house is caught too). A violation is an AssertionError out of turn(): nothing is sent, the pending row keeps the error, catch-up retries.

FUNNEL CONTINUITY: luna_brain.funnel_stage names the first gate requirement_scoreboard already found open -- no second state machine. turn() persists card.stage/stage_at, the scoreboard carries stage/stage_since, and the FUNNEL CONTINUITY prompt rule resumes there instead of restarting.

Verified: tests/test_wa_luna_dialog_rules.py (38 tests, including a 100-clinic result set producing 5 positions + remainder 95 + both branches, the pool and narrow branches recorded on the card, a stage persisted and resumed across two turns, an invented and a real-but-unsearched clinic both rejected, and ordinary German not mistaken for a clinic name) plus the full offline suite: 1950 passed, 127 skipped, 70 deselected, 5 warnings in 168.94s.
<!-- SECTION:FINAL_SUMMARY:END -->
