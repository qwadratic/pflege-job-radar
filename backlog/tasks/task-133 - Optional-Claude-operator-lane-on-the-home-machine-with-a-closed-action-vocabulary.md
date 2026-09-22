---
id: TASK-133
title: >-
  Optional: Claude operator lane on the home machine with a closed action
  vocabulary
status: Done
assignee: []
created_date: '2026-09-21 01:24'
updated_date: '2026-09-21 09:08'
labels:
  - wa-transport
dependencies:
  - TASK-130
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: low
type: feature
ordinal: 141000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M12, explicitly optional. Scope is repair only: re-link after a logout, fix a selector, re-run provisioning. NEVER on the send path.

Inbound candidate text is untrusted content, so there is no model on the hot path. The operator lane exists to do the three manual chores that otherwise need a human at the home machine at an awkward hour, and nothing else.

The vocabulary is closed: no verb carries free text. open_chat takes a job id, not a phone. insert_payload takes a job id and the executor sha256-verifies the inserted text after insertion. One send lease per job.

Quota matters: any Claude on the home machine competes with Luna for the same account pool, and Luna is already capped at 20 calls per hour. Separate account, budget cap, escalation cap per hour.

Do this last or not at all. It is the only task in the plan that may be dropped without consequence.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 No verb in the action schema accepts free text, and open_chat takes a job id rather than a phone number
- [ ] #2 insert_payload sha256-verifies the inserted text after insertion and fails loudly on a mismatch
- [ ] #3 A fuzz test feeds 200 malformed actions and asserts zero reach the device
- [ ] #4 The lane runs on a separate Claude account with a budget cap and an escalation cap per hour, so it cannot starve Luna
- [ ] #5 The lane holds no WhatsApp credential and is never invoked on the send path
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
CLOSED AS OBSOLETE by decision-8 (2026-09-21), not done. The machine this lane would run on is not what the task assumed.

It is not a private Mac in Ivan home. It is the colleague Ubuntu worker box (macmini-worker1, 24.04.5 LTS), and a root-installed Cursor cloud agent worker already runs remote-dispatched jobs there as the same uid we log in as (cursor-worker.service, User=cursorworker1). cursorworker1 is also in the lxd group with a privileged container running, so "we have no sudo" is not isolation.

Adding a third model lane on that account buys three manual chores (re-link, repair a selector, re-run provisioning) and costs a Claude credential on a machine that already hosts somebody else remote-dispatched agent -- next to an unrestricted root key into our VPS. The task own text called it "the only task in the plan that may be dropped without consequence". It is dropped.

The launchd assumption it carried is void anyway: supervision on that machine is systemd --user plus loginctl enable-linger. No acceptance criterion is checked: none was verified.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Obsolete, not implemented. Closed by decision-8: the target machine is a shared colleague worker box already running a root-installed Cursor cloud agent as our uid, so a third model lane there is not worth its credential. Explicitly optional by its own text; dropped.
<!-- SECTION:FINAL_SUMMARY:END -->
