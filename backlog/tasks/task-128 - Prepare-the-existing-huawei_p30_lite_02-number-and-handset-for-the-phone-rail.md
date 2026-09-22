---
id: TASK-128
title: Prepare the existing huawei_p30_lite_02 number and handset for the phone rail
status: Done
assignee: []
created_date: '2026-09-21 01:23'
updated_date: '2026-09-21 09:08'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: chore
ordinal: 136000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M3, forced by decision-6 and revised by the plan ADDENDUM item 4 ("decisions, 2026-09-21 (Ivan)", written after decision-6 and after the first version of this task).

NO NEW SIM. The +49 number already on huawei_p30_lite_02 becomes the bot sender. It is a candidate-role persona number ("Babu22"), not the WABA number, so "the Valentyn NDT number cannot move" does not apply to it: NDT stays Cloud-API-registered, keeps its 29 APPROVED recruitment_* templates and the live nginx webhook at /candidate-action/webhooks/meta/whatsapp, and is not touched here.

Scope is therefore "prepare the existing account", not "procure a SIM":

1. Account health is UNKNOWN. The 2026-08-08 ban-check tasks on this handset were cancelled with no result, and the device has been offline since 2026-09-19. The FIRST physical action is to confirm the account is alive and unrestricted. If it is not, this whole milestone changes shape and the answer is needed before anything else is done.
2. The WhatsApp profile still carries the "Babu22" persona name and 111 WhatsApp tasks from 2026-08-07..10 in the candidate role against Valentyn NDT. It must be re-identified as the business, and the fate of the old persona chats decided, before a single candidate sees the number.
3. Handset is huawei_p30_lite_02, the idle one. Explicitly NOT huawei_p30_lite_01: that one runs the colleague ChatGPT lead research at roughly 77k requests per day and is not ours. Whether _02 can actually be released has to be confirmed with the colleague, not assumed; if it cannot, the fallback is a bought handset AND a new number, which puts the SIM back on the table.

The two-step verification PIN is an outage risk disguised as a checkbox. Losing it is a hard 7-day lockout with no way to expedite, and the recovery email is the only thing that shortens it.

GDPR: do not sync the handset address book. The consumer app uploads the whole address book to Meta including non-consenting third parties, which German DPAs hold unlawful for business use. wa.me deep links work without a saved contact, so upsert_contact is dropped from the design entirely.

This task ends when the number can send and receive by hand under a business identity. It does not link WhatsApp Web, does not install a tunnel and does not touch the colleague services.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Release of huawei_p30_lite_02 is confirmed in writing with the colleague, or a replacement handset is bought instead; the outcome is recorded either way
- [ ] #2 The existing +49 number on _02 is confirmed alive and unrestricted on WhatsApp (the 2026-08-08 ban checks were cancelled, so this is verified, not assumed) and is written down as the rail number
- [ ] #3 The WhatsApp profile is re-identified from the Babu22 persona to the business identity, and the fate of the old persona chats is decided and recorded, before any candidate sees the number
- [ ] #4 Two-step verification is on with a PIN AND a recovery email, both stored where they are findable at 3am by someone who is not the person who set them up
- [ ] #5 Address-book sync is off and no candidate number is saved as a contact on the device
- [ ] #6 The handset is confirmed to send and receive by hand on that number, with sleep, battery optimisation and autostart settings recorded
- [ ] #7 Nothing on the colleague port-8791 stack is enabled, flipped or configured, and CONSUMER_WHATSAPP_MODE is not touched
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
CLOSED AS OBSOLETE by decision-8 (2026-09-21), not done. This task prepares the wrong handset, and following it would have pointed an agent at the colleague live revenue lane.

huawei_p30_lite_02 is serial L2N4C19B14054035 -- the ChatGPT Plus farm phone, under live USB automation. His own code binds the name: ~/clinic-dispatcher/tools/farm_usb.py:55-56 comments "huawei_p30_lite_02 (the Plus-account phone on the Mac mini cable). 01 = L2N4C19B14054874." and then sets DEFAULT_SERIAL = "L2N4C19B14054035". His WhatsApp code refuses that serial by construction: apps/wa_phone/config.py:10 FORBIDDEN_SERIALS, device.py raises WrongPhone. The "offline since 2026-09-19" signal that made _02 look idle was reassignment, not availability: the on-device agent stopped when the farm moved to USB on 2026-09-20.

The WhatsApp lane runs on huawei_p30_lite_01 = L2N4C19B14054874 (config.py:9 HUAWEI_01_SERIAL).

The number half is also wrong. The only handset identity ever captured on either phone is for ...035: a +48 (Poland) account, profile name "Babu22" (phone_agent.sqlite task_f60da4c83e63, 2026-08-07). The "+49" in this task, in decision-7 and in the plan ADDENDUM item 4 has no evidence behind it. The MSISDN on ...874 is recorded nowhere read-only on either machine.

Replaced by TASK-136 (device -> serial -> MSISDN map, and settle the ...874 identity), which is blocking. The parts of this task that survive -- two-step PIN with a recovery email, no address-book sync, no upsert_contact, touch nothing on the colleague stack -- were carried into TASK-136 rather than left here. No acceptance criterion is checked: none was verified.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 02:46
---
Retitled and rescoped from "Acquire and prepare the new SIM ..." to follow the plan ADDENDUM item 4 (2026-09-21), which was appended to /home/claude/plans/2026-09-20-wa-home-transport-plan.md at 01:30 -- after decision-6 (01:18) and after this task was created (01:23). decision-6 "Decision" item 3 still says "A NEW SIM and a new number" and now contradicts the addendum. decision-6 cannot be amended with the backlog CLI (there is no `decision edit`), so that record is left as filed and needs Ivan: either a superseding decision or a correction.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Obsolete, not implemented. Closed by decision-8: huawei_p30_lite_02 is the ChatGPT farm phone (L2N4C19B14054035), refused by the colleague own code; the WhatsApp lane is on _01 (L2N4C19B14054874) and its sender MSISDN is unrecorded. Replaced by TASK-136.
<!-- SECTION:FINAL_SUMMARY:END -->
