---
id: TASK-127
title: >-
  Joint pacing policy on one shared handset, at the mini-side constants, with a
  per-number cap
status: To Do
assignee: []
created_date: '2026-09-21 01:23'
updated_date: '2026-09-22 06:09'
labels:
  - wa-transport
dependencies:
  - TASK-113
  - TASK-120
  - TASK-124
  - TASK-136
  - TASK-137
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 135000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M10, rescoped by decision-8 (2026-09-21) from "pick our own bridge-rail constants" to "adopt theirs as the floor and fix their two holes".

The handset is SHARED. Two senders pace one consumer account, and neither can see the other traffic from its own side. So there is one policy, it is the stricter of the two, and our side may only ever be slower.

THEIR CONSTANTS ARE ALREADY STRICTER than the ADDENDUM asked for, and they are the floor (their config.py:38-49, humanize.py:20-22): 9 to 20 Europe/Berlin; 240 to 600 s log-uniform gaps between first touches; 10 first touches per day; 4 per hour; 20 to 90 s reply think time; Sunday blocked. The ADDENDUM asked for 1 per 90 s, 12/hour, 20/day rising to 50 -- looser on every axis. Adopt the mini-side numbers. Do not invent a middle.

TWO HOLES IN THEIR IMPLEMENTATION, ours to fix rather than inherit:
1. The daily cap is GLOBAL, not per number, and it is counted on a UTC day while pacing runs Europe/Berlin -- so the cap window resets at 02:00 local in summer (their cli.py, store.py). We need a per-sender-number cap counted on the pacing timezone day.
2. QUIET HOURS APPLY TO FIRST TOUCHES ONLY. within_active_hours is referenced once, inside the first_touch branch, so replies bypass the window entirely. Observed live: an outbound reply at 07:53 Europe/Berlin. Quiet hours apply to every outbound, reply included.

Division of labour unchanged: the server owns the schedule (campaign.py Window plus claimed_at-counted batching, which already survives restarts). The executor on the remote machine owns a floor and a fuse -- always slower than asked, never faster, and refuses past its own daily cap. The fuse is non-overridable by the request so a server bug cannot open it.

Error classification at campaign.py stays unchanged: BridgeError carries .status_code, which is why rail_parked maps to 429 and therefore failed with ownership restored.

The quiet-hours house rule is now settled by adoption rather than by asking: the mini-side window is the rule because it is the stricter one and it is the one already running on that handset. Record it; if Ivan overrides it, record that instead. Do not invent a third.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Bridge-rail pacing uses the mini-side constants as the floor: 9 to 20 Europe/Berlin, 240 to 600 s gaps between first touches, 10 first touches per day, 4 per hour, Sunday blocked, proven under a fake clock
- [x] #2 The daily cap is enforced PER SENDER NUMBER, not globally, and is counted on the pacing timezone day rather than the UTC day, with a boundary test across the summer 02:00 local case
- [x] #3 Quiet hours apply to every outbound on the rail including replies and nudges, proven by a test that attempts a reply outside the window and asserts refusal
- [x] #4 The executor fuse is non-overridable by the request: a request asking for a shorter gap or a higher cap is honoured only in the slower direction
- [x] #5 Exceeding a cap yields a loud rail_parked style failure with ownership restored; it never silently drops a recipient and never silently sends anyway
- [ ] #6 The suppression store is consulted before every campaign send on both rails, and a campaign refuses to start when it is unavailable
- [x] #7 Meta-rail pacing defaults are unchanged, proven by a test on the Meta path
- [ ] #8 docs/whatsapp.md records the adopted house rule, states that it is the mini-side window because it is the stricter one, and names whose constants they are
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: substantially built in bridge/governor.py (shipped 2026-09-21 as part of TASK-146), correctly stays To Do for two real remaining gaps. Built: the mini-side constants adopted whole as the floor (bridge/governor.py:9-22, read from the colleague's config.py PACING at commit ea82a51: 9-20 Europe/Berlin, 240-600s first-touch gaps, 10/day, 4/hour, Sunday blocked) -- AC#1, tested (tests/test_bridge_executor.py::test_the_first_touch_caps_are_the_colleagues_numbers). Per-number, pacing-timezone-day cap (their two holes fixed, not inherited) -- AC#2, tested (test_the_governor_refuses_past_the_per_number_daily_cap, test_the_daily_cap_is_counted_on_the_pacing_timezone_day). Quiet hours on every outbound including replies -- AC#3, tested (test_the_governor_refuses_a_reply_outside_active_hours, test_asking_to_bypass_quiet_hours_is_refused_not_ignored). Fuse non-overridable, only slower ever honoured -- AC#4, tested (test_a_request_cannot_shorten_the_gap, test_a_request_cannot_raise_the_cap). Loud rail_parked with ownership restored, never silent -- AC#5, tested via bridge/errors.py + campaign.py's 4xx classification (shared with TASK-120). Meta-rail pacing untouched -- AC#7, campaign.py's window/batch pacing (tests/test_wa_campaign_sender.py) was not touched by this work. NOT MET: AC#6, the compound criterion 'suppression consulted before every campaign send on both rails AND a campaign refuses to start when it is unavailable' -- the first half is true (TASK-113: campaign.send_one calls SUP.assert_not_suppressed), the second half is not (same gap as TASK-113 AC#8: no campaign-start refusal on a missing/empty/unreachable suppression store). AC#8, docs recording the adopted house rule with its numbers and the 'stricter, so it wins' reasoning -- docs/whatsapp.md:79 only namechecks TASK-127 in one sentence, no numbers, no subsection. Full offline suite green: 2312 passed.
<!-- SECTION:NOTES:END -->
