---
id: TASK-112
title: Falsify the WhatsApp Web message-id premise before any executor is built
status: Done
assignee: []
created_date: '2026-09-21 01:19'
updated_date: '2026-09-21 09:08'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: spike
ordinal: 120000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M1. This is the load-bearing bet of the whole phone-rail project and nothing else may be built until it is settled.

The chosen v1 actuator is WhatsApp Web driven as a linked device, and the only reason it was chosen over the adb path is the premise that WhatsApp Web exposes a real, unique, stable message id (the DOM `data-id`) in ONE id space for both directions. If that holds, `wa_messages.wamid UNIQUE`, reply-context matching, media claims and `wa_campaign_sends.wamid` all keep their exact current meaning with zero schema change, and tick state gives real delivery statuses.

The premise is UNVERIFIED. It was never tested; we never connected to the home machine. If it is false the actuator must be re-picked (synthesized ids plus a dedup table, or the adb path) BEFORE the contract, the executor or the adapter are written, because the fallbacks converge on a different shape.

Throwaway code on a burner number. Nothing lands in app/ or bridge/.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 On a burner number linked as a WhatsApp Web companion, a data-id is captured for at least 100 sent and 100 received messages
- [ ] #2 The capture spans text, image, PDF, voice note and a quoted reply, and records for each whether the id is present and unique
- [ ] #3 The same messages are re-read after a page reload, a browser restart and a full relink, and the verdict stable / not stable is recorded with the evidence
- [ ] #4 It is recorded explicitly whether outgoing and incoming ids share one id space, and whether an id survives as a usable reply-context reference
- [ ] #5 Any anomaly the session shows under Playwright automation (warning banner, forced logout, challenge screen, session drop) is recorded, including none observed
- [ ] #6 A written verdict is filed: premise holds and WhatsApp Web stays the v1 actuator, or premise dead and the named fallback actuator is synthesized ids plus a dedup table, or the adb path
- [ ] #7 No file is added under app/ or bridge/; the probe code is throwaway and lives outside the package
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
CLOSED AS OBSOLETE by decision-8 (2026-09-21), not done. The premise this spike existed to falsify is answered by the ground, without the spike.

There is no WhatsApp Web on the phone rail and there is no DOM data-id to read. The machine is a Ubuntu box (macmini-worker1, 24.04.5 LTS on Apple hardware), and the lane that actually runs there -- ~/wa-phone-outreach/apps/wa_phone, 1955 lines, live since 2026-09-20 15:53 -- drives the consumer WhatsApp app over USB adb. That is the plan own documented fallback actuator, and it shipped.

The verdict this spike would have produced is therefore already in: the premise is DEAD. There is no message identifier of any kind on that stack (grep msg_id|message_id|wamid|@c.us|idempot over the package returns one docstring hit), so the id space is ours: the client_msg_id we mint is the only id an outbound message has. decision-8 item 5 renegotiates the invariant accordingly -- a verified delivery tick replaces the confirmed provider message id, on this rail only.

TASK-134 (lane-ownership decision record) replaces this task at the head of the dependency chain; TASK-119 and TASK-130 were repointed at it. No acceptance criterion is checked: none was verified, the task is void rather than complete.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Obsolete, not implemented. Closed by decision-8: the actuator is adb against the consumer app on a Ubuntu box, not WhatsApp Web, so there is no data-id premise left to falsify and no id space at all. Replaced at the head of the chain by TASK-134.
<!-- SECTION:FINAL_SUMMARY:END -->
