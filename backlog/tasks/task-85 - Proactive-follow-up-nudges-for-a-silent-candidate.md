---
id: TASK-85
title: Proactive follow-up nudges for a silent candidate
status: Done
assignee: []
created_date: '2026-09-13 13:13'
updated_date: '2026-09-13 13:22'
labels: []
dependencies: []
ordinal: 85000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Real system re-engages a candidate who has gone silent after our last message, at tiered intervals (15m/1h/4h, capped per streak). This harness only ever replies today -- explicitly named as a gap. Build a scaled-down equivalent: fixed, reviewable nudge text (not a model call -- an unprompted, system-initiated message is not what the existing turn() contract, built around 'the candidate just said X', was designed for), tiered and capped, gated through the same 24h-window/AUTOSEND send path everything else uses.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New tiered timing (default 15/60/240 minutes, configurable) and a per-streak cap (default 4, matching the real system's own cap) on how many nudges a silent thread gets before this harness stops trying
- [x] #2 A streak resets the moment the candidate replies (ball_for() flips back to 'us') -- tracked without a new count column that could drift, derived from nudges sent since the candidate's own last message
- [x] #3 Sends go through the exact same app.wa.api._send_and_record()/24h-window gate as every other outbound path -- no second copy of that logic
- [x] #4 A stopped (opted-out) thread is never nudged
- [x] #5 New CLI (python -m app.wa.luna.followups) usable standalone, same manual/cron-invoked convention as catchup.py -- no systemd timer installed as part of this task
- [x] #6 Unit tests cover: tier progression, the streak cap, a reply resetting the streak, a stopped thread being skipped, the 24h-window gate applying
- [x] #7 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/store.py: wa_followups_sent(phone, tier, sent_at) + record_followup_sent/followup_tiers_sent_since/candidate_phones. app/wa/config.py: WA_FOLLOWUP_TIERS_MINUTES (default 15,60,240), WA_MAX_FOLLOWUPS_PER_STREAK (default 4), WA_FOLLOWUP_NUDGE_DE. app/wa/luna/followups.py: run()/main() -- eligibility is ball_for()=='them' + not stopped + _eligible_tier() (tiers fire strictly in order, one at a time, streak derived from followups sent since the candidate's own last message so a reply naturally resets it without a drift-prone counter). Sends go through api.send_and_record() (renamed from _send_and_record -- a second real caller outside api.py made the underscore-prefix wrong), so the TASK-70 window gate applies automatically. 11 new tests, two real test-authoring bugs caught and fixed while writing them (a wamid collision between a manually-inserted row and the fake client's own counter; a chronologically-backward timestamp setup that silently disproved the reset test it was supposed to prove). Offline suite: 1133 passed, same 5 pre-existing unrelated failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/luna/followups.py gives silent candidates a tiered, capped nudge (15/60/240 min, matching the real system's own cadence), using fixed reviewable text rather than a model call since an unprompted system-initiated message doesn't fit the existing turn() contract. Reuses the exact same send/24h-window path as every other outbound message -- no parallel send logic to keep in sync.
<!-- SECTION:FINAL_SUMMARY:END -->
