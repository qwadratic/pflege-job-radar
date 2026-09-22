---
id: TASK-152
title: >-
  COUNT rule: board-wide total never reaches the evidence set; OFFER_LIMIT
  anchors laundered approximations
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-22 05:46'
updated_date: '2026-09-22 05:46'
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
- [ ] #1 The board-wide open-jobs total and clinic total the harness already computes (market_snapshot) are threaded into the counts check_reply sees on a real turn, and a truthful statement of either is accepted
- [ ] #2 STRUCTURAL numbers (OF.OFFER_LIMIT, the display cap) are represented separately from EVIDENCE numbers (real market facts); a structural number may satisfy an exact statement about how many positions are shown but never anchors an approximation marker
- [ ] #3 'rund 10 offene Stellen' with only the display cap (5) present in evidence is rejected; the approximation tolerance cannot stretch a single anchor into a different order of magnitude
- [ ] #4 The audit counterexample ('nur diese 5 Kliniken' against a true 224) is still rejected with the display cap present
- [ ] #5 The display cap still passes as an exact statement about the shown list ('diese 5 Stellen zeige ich Ihnen')
- [ ] #6 At least four live turns (including one Bavaria-wide question) are run against a scratch copy of data/wa.sqlite with check_reply's evidence wrapped and printed; no message is sent; each turn's action is reported
- [ ] #7 Offline suite (PFLEGE_TESTS_OFFLINE=1 pytest -m 'not network and not llm') stays green with no existing test weakened
<!-- AC:END -->
