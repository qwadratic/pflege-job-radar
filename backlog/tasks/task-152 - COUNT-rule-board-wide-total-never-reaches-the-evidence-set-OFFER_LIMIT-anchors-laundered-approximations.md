---
id: TASK-152
title: >-
  COUNT rule: board-wide total never reaches the evidence set; OFFER_LIMIT
  anchors laundered approximations
status: Done
assignee:
  - '@claude'
created_date: '2026-09-22 05:46'
updated_date: '2026-09-22 06:03'
labels: []
dependencies:
  - TASK-151
ordinal: 160000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-151 fixed the COUNT rule's number parsing and approximation tolerance, but was verified only by hand-injecting counts={2462} directly into check_reply -- never against a real turn. An Opus reviewer ran the bot live 2026-09-22 and found two failure modes TASK-151's tests never exercised: (1) the board-wide open-jobs total (market_snapshot.open_jobs, computed by our own harness from the live board -- 2399 today from 2502 rows/407 clinics) is never assembled into the counts passed to check_reply during a real turn, so a truthful Bavaria-wide figure is rejected as unsupported and the thread gets reply_blocked_escalated; 2 of 7 live runs on the same Bavaria-wide question ended that way, and all 7 lost their figure. (2) Because the fix now accepts approximations within a tolerance of ANY evidence number, and OF.OFFER_LIMIT (the per-message display cap, currently 5) is unconditionally injected into the allowed counts, a fabricated 'rund 10 offene Stellen' or 'gut 5 offene Stellen' passes with zero real evidence -- the display cap is a UI constant we chose, not a fact about the market, and must not serve as an approximation anchor. Live action distribution the reviewer measured on the same question/board: 4x reply_after_correction, 2x clean, 2x reply_blocked_escalated (of 7) -- three different outcomes for the same sentence, which is itself the defect to close, not just the individual rejections.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The board-wide open-jobs total and clinic total the harness already computes (market_snapshot) are threaded into the counts check_reply sees on a real turn, and a truthful statement of either is accepted
- [x] #2 STRUCTURAL numbers (OF.OFFER_LIMIT, the display cap) are represented separately from EVIDENCE numbers (real market facts); a structural number may satisfy an exact statement about how many positions are shown but never anchors an approximation marker
- [x] #3 'rund 10 offene Stellen' with only the display cap (5) present in evidence is rejected; the approximation tolerance cannot stretch a single anchor into a different order of magnitude
- [x] #4 The audit counterexample ('nur diese 5 Kliniken' against a true 224) is still rejected with the display cap present
- [x] #5 The display cap still passes as an exact statement about the shown list ('diese 5 Stellen zeige ich Ihnen')
- [x] #6 At least four live turns (including one Bavaria-wide question) are run against a scratch copy of data/wa.sqlite with check_reply's evidence wrapped and printed; no message is sent; each turn's action is reported
- [x] #7 Offline suite (PFLEGE_TESTS_OFFLINE=1 pytest -m 'not network and not llm') stays green with no existing test weakened
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Reproduce both findings live against the real board (real Supabase data, no LLM needed for the
   check_reply-level bugs since they are pure functions over text+evidence): confirm the true
   board-wide open_jobs total is rejected by GR.check_reply under the OLD counts assembly (offer-only),
   and confirm 'rund 10'/'knapp 10'/'gut 5' pass with zero real evidence under the OLD _false_counts
   (OF.OFFER_LIMIT folded into the same set used for both exact and approximate matching).
2. Fix Finding 1: in GR.turn_evidence, add the board-wide facts the harness already computes
   (len(board_clinic_names()), market_snapshot's open_jobs, matching_clinics_count) to `counts`
   unconditionally, not only inside `if offer:`.
3. Fix Finding 2: split GR._false_counts into an `evidence` set (may anchor an approximation marker)
   and a `structural` set (OF.OFFER_LIMIT, the bubble's own total_positions/len(named) -- may only
   satisfy a bare EXACT claim, never a marker). Update GR.check_reply's call site accordingly.
4. Add tests/test_wa_luna_dialog_rules.py coverage: true board-wide total passes pre-offer; a
   fabricated total is still rejected; the audit counterexample still rejects through the full
   turn_evidence pipeline; the display cap cannot anchor rund/knapp/gut; the display cap still passes
   as a bare exact statement.
5. Re-run the reproduction against the fixed code to confirm both findings close, run the full
   offline suite, then run >=4 live turns (real board, real `claude -p` subprocess, scratch copy of
   data/wa.sqlite, check_reply wrapped to print evidence, nothing sent) including a Bavaria-wide
   question, and report each turn's action.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Reproduced both findings live against the real board (Supabase, current live totals: 2469 open jobs,
278 clinics) before fixing, by reconstructing the pre-fix counts-assembly/_false_counts logic inline
and running it through GR.check_reply against real snapshot data:
- Finding 1: 'Bayernweit habe ich aktuell 2469 offene Stellen im Angebot.' against the OLD
  offer-only counts (empty pre-offer) raised COUNT ('Drop the figure or replace it with one of
  these: []'); against the same sentence with GR.turn_evidence's new unconditional board-wide
  counts it is accepted.
- Finding 2: with zero real evidence and only structural {0, OF.OFFER_LIMIT}, the OLD
  _false_counts passed 'rund 10', 'knapp 10' and 'gut 5 offene Stellen' (laundering hole); the NEW
  structural/evidence split rejects all three, while 'Ich zeige Ihnen diese 5 Stellen.' (bare exact)
  still passes.
- Audit counterexample re-verified against the real board (offer clinics_total=32, 'nur diese 5
  Kliniken') -- still rejected.
Fix: app/wa/luna/grounding.py turn_evidence() now adds len(board_clinic_names()),
snapshot['open_jobs'] and snapshot['matching_clinics_count'] to counts unconditionally (previously
only inside `if offer:`); _false_counts(text, evidence, structural) now anchors approximation
markers on `evidence` only, and matches bare exact figures against evidence | structural.
check_reply's call site renamed its local set to `structural_counts` for clarity. No change to
app/wa/luna_brain.py or app/wa/luna/offer.py was needed -- market_snapshot already computed
open_jobs/matching_clinics_count every turn, and OF.OFFER_LIMIT already existed; the gap was purely
in how grounding.py consumed them.
Tests: tests/test_wa_luna_dialog_rules.py grew by 5 (136 -> 141): board-wide total pre-offer, a
fabricated total still rejected, the audit counterexample through the full pipeline, the display cap
cannot anchor rund/knapp/gut, the display cap still passes as a bare exact statement. No existing
test was weakened; all 20+ existing COUNT-rule tests (German-dot parsing, decimal-comma, each
approximation kind, the audit-D/over-4000 fabrication tests) still pass unchanged.
Offline suite: PFLEGE_TESTS_OFFLINE=1 pytest -q -m "not network and not llm" ->
2312 passed, 127 skipped, 70 deselected, 5 warnings in 170.81s (up from the 2307-passing baseline by
exactly the 5 new tests).
Live verification: 4 live turns via LB.turn() with a REAL claude -p subprocess Client, against a
scratch copy of data/wa.sqlite (data/wa.sqlite itself untouched) and the real live board, GR.check_reply
wrapped to print bubbles/allowed/counts/remaining on every call -- no message sent anywhere (no bridge,
no Meta call). Two independent fresh threads asked the Bavaria-wide question verbatim; both got the
true live figure (2469/2.469 Stellen) accepted on the first pass, no correction, no escalation -- the
previously-observed 2/7 reply_blocked_escalated flakiness did not reproduce in this run. Two more
threads (a general opener naming a city, and an explicit clinic-count question) also passed cleanly
with the true 278/2469 figures. Actions observed: answer_then_ask_qualification, ask_region (x2),
ask_qualification -- none were reply_after_correction or reply_blocked_escalated.
Residual observed, not fixed (out of this task's scope): the 'at_least' marker kind (ueber/mehr
als/gut) is unbounded above by TASK-151's own pinned design ('a floor, not a point estimate: true
whenever some evidence number is >= N') -- so once real evidence is always present (this fix's own
change), a small claim like 'ueber 40 Kliniken' about one city can trivially pass against an
unrelated large board-wide number. Observed live once (a per-city clinic estimate passed against the
board-wide clinic total). Not a re-introduction of Finding 2 (that was specifically the structural
display cap, now excluded) and changing 'at_least' semantics further would touch a pinned TASK-151
decision/test -- flagged for Ivan rather than silently guarded (CLAUDE.md).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna/grounding.py: fixed the two holes the round-2 live audit found in TASK-151's COUNT-rule
fix. (1) turn_evidence() now adds the board-wide facts the harness already computes -- board_clinic_names()
length, market_snapshot's open_jobs and matching_clinics_count -- to `counts` unconditionally, not only
inside `if offer:`, so a truthful Bavaria-wide answer is supported before any funnel gate is settled
(it was rejected live on 2/7 runs). (2) _false_counts(text, evidence, structural) now anchors an
approximation marker (rund/etwa/ca./ueber/knapp/...) only on real market evidence; OF.OFFER_LIMIT and
the bubble's own position/name counts are STRUCTURAL -- true of the message or a constant we picked,
not a market fact -- and may only satisfy a bare exact claim, closing the laundering hole where 'rund
10'/'gut 5' passed with zero real evidence because the 5-position display cap sat inside their
tolerance. Invention detection is unchanged: the audit-D counterexample ('nur diese 5 Kliniken'
against a true count) still rejects through the full pipeline.
Verified: reproduced both findings live against the real board before fixing (reconstructing the old
logic inline), then against the fix; tests/test_wa_luna_dialog_rules.py grew 136 -> 141, all passing,
no existing test weakened; full offline suite 2312 passed (127 skipped, 70 deselected) in 170.81s;
4 live turns via a real `claude -p` subprocess against a scratch copy of data/wa.sqlite and the real
board (2 Bavaria-wide, 2 others), check_reply's evidence wrapped and printed, nothing sent -- all 4
accepted the true figures on the first pass (no correction, no escalation).
<!-- SECTION:FINAL_SUMMARY:END -->
