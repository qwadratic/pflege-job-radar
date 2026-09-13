---
id: TASK-76
title: Per-candidate LLM call rate limit (parity with CATCHUP_MODEL_RUNS_PER_HOUR)
status: In Progress
assignee: []
created_date: '2026-09-13 11:14'
updated_date: '2026-09-13 11:25'
labels: []
dependencies: []
ordinal: 76000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Recon found our harness has zero rate limit on how often the claude CLI is invoked, per candidate or globally -- confirmed by grep. The real system caps proactive model calls at 2/candidate/hour. Add an equivalent backstop for our harness: a per-phone hourly cap on luna brain invocations, defaulting to a generous number so normal conversations are never affected, existing specifically to stop runaway/abuse cost. On cap-hit, the message must still be stored and nothing lost -- rely on the new catch-up driver (separate task) to backfill the reply shortly after.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New usage log table records every actual luna brain invocation with phone + timestamp
- [x] #2 WA_LUNA_MAX_CALLS_PER_HOUR env var (sane non-zero default) caps invocations per phone per rolling hour
- [x] #3 Hitting the cap skips the brain call for that turn without losing the inbound message, and is distinguishable in the result (not indistinguishable from a normal no-send)
- [x] #4 Cap check happens in code (app/wa/api.py), never inside luna_brain.turn(), matching the existing pattern of compliance/safety decisions living in code not the model
- [x] #5 Unit tests cover under-cap (calls proceed), at-cap (skipped), and rolling-window expiry (an old call ages out of the window)
- [x] #6 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/store.py: wa_luna_calls(phone, at) + record_luna_call/count_recent_luna_calls (rolling 1h window). app/wa/config.py: WA_LUNA_MAX_CALLS_PER_HOUR, default 20 (generous backstop, not a conversational throttle), 0 disables. Checked in process_owed_turn() before dispatching to the brain -- over cap finishes the claim as skipped_rate_cap (reclaimable) and returns status=rate_limited without losing the inbound message.
<!-- SECTION:NOTES:END -->
