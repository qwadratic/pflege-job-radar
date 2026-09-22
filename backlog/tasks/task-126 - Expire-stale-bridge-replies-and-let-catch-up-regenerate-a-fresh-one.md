---
id: TASK-126
title: Expire stale bridge replies and let catch-up regenerate a fresh one
status: To Do
assignee: []
created_date: '2026-09-21 01:22'
updated_date: '2026-09-21 09:15'
labels:
  - wa-transport
dependencies:
  - TASK-117
  - TASK-120
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: medium
type: feature
ordinal: 134000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M6. A reply delivered six hours late is a real candidate-experience defect, and the phone rail makes it likely: the handset comes back at 06:00 still holding a 02:00 answer.

There is no staleness rule today. WA_OUTBOX_MAX_AGE_MIN (20) adds one.

Deliberately NOT a durable outbox. The repo already has the machinery: a raised send sets the claim to skipped_error (`app/wa/api.py:866`), which `store.py:302` documents as reclaimable, and pflege-wa-catchup.timer re-drives process_owed_turn. Expiry reaches that same state through that same function instead of building a second state machine in app/wa/.

The fresh reply is the point. The candidate gets an answer written now, not a stale bubble written hours ago.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A bridge reply not delivered within WA_OUTBOX_MAX_AGE_MIN is cancelled rather than sent late
- [ ] #2 The cancelled reply gets a bridge-origin failed status row so luna_brain drops it from Luna history, using the existing filter at luna_brain.py:825-834 with no edit there
- [ ] #3 The claim is left reclaimable so the next catch-up run regenerates new reply text rather than resending the stale bubbles
- [ ] #4 A test asserts the regenerated text is produced fresh and that exactly one message reaches the transport across the expire-and-regenerate cycle
- [ ] #5 Expiry applies to the bridge rail only; Meta-rail behaviour is unchanged
<!-- AC:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:15
---
decision-8 (2026-09-21): KEEP as written, and more important under a 202-first contract: expiry is what stops a 02:00 answer landing at 06:00 once most sends resolve asynchronously.
---
<!-- COMMENTS:END -->
