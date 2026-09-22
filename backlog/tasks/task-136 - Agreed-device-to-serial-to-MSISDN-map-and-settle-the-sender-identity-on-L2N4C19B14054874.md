---
id: TASK-136
title: >-
  Agreed device to serial to MSISDN map, and settle the sender identity on
  L2N4C19B14054874
status: To Do
assignee: []
created_date: '2026-09-21 09:10'
updated_date: '2026-09-22 06:10'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-21-macmini-revision.md
priority: high
type: chore
ordinal: 144000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
BLOCKING. Replaces TASK-128, which prepared the wrong handset. Nothing is pinned to the phone rail except Ivan own test number, and no candidate is messaged from that handset, until this is answered.

Two devices, two serials, and a naming convention nobody wrote down until it had already misled us:
- huawei_p30_lite_02 = L2N4C19B14054035 = the ChatGPT Plus farm phone, under live USB automation. FORBIDDEN. His own WhatsApp code refuses it (apps/wa_phone/config.py:10 FORBIDDEN_SERIALS, device.py raises WrongPhone).
- huawei_p30_lite_01 = L2N4C19B14054874 = the phone that runs the WhatsApp lane (config.py:9 HUAWEI_01_SERIAL).

The sender identity on ...874 is UNVERIFIED. It is recorded nowhere read-only on either machine: his store schema has no self/me row, his config holds serials and pacing but no account, and his doctor command prints model, IME, WhatsApp version, queue and lock and never a number. The only handset identity ever captured on either phone is for ...035 -- a +48 (Poland) account, profile name "Babu22", phone_agent.sqlite task_f60da4c83e63, 2026-08-07. Every "+49" in decision-7, in plan ADDENDUM item 4 and in docs/whatsapp.md came from that wrong handset and has no evidence behind it.

Why this can break production: registering a number in the consumer WhatsApp app deactivates it on the Cloud API. We cannot rule that out from our side because our Meta rail has been idle since 2026-09-17, so a silent takeover would have produced no symptom. "Our rail still works" is not available as evidence.

Two read-only settles, both needing a human:
1. Graph: GET /v25.0/{META_WHATSAPP_PHONE_NUMBER_ID}?fields=display_phone_number,verified_name.
2. Ask the colleague to re-run his own proven read-only identify task (task_f60da4c83e63 goal text) against huawei_p30_lite_01 rather than _02.

Blocking rule: until both answers are in hand AND DIFFER, the rail carries nothing. If the answer is Valentyn personal number, the rail is dead -- at the first milestone, not at ramp-up. Whether we need a SIM after all is re-opened by decision-8, not re-answered.

Carried over from TASK-128 because they survive the handset correction: two-step verification with a PIN AND a recovery email, stored where someone who did not set them up can find them at 3am; no address-book sync; upsert_contact dropped from the design entirely (wa.me deep links work without a saved contact); nothing on his stack is enabled, flipped or configured.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A written map of device name to serial to role exists in the repo, naming L2N4C19B14054035 as forbidden and L2N4C19B14054874 as the lane, and the colleague has confirmed it
- [ ] #2 The MSISDN registered to WhatsApp on L2N4C19B14054874 is established by the colleague own read-only identify task and written down
- [ ] #3 The WABA display_phone_number is read from Graph and compared against it, and the verdict same number or different number is recorded with both sources
- [ ] #4 If the two are the same number, or the handset number is a personal one, the rail is declared dead and the decision is escalated rather than worked around
- [ ] #5 Two-step verification is on with a PIN and a recovery email, both stored where someone other than the person who set them up can find them
- [ ] #6 Address-book sync is off, no candidate number is saved as a contact on the device, and upsert_contact is absent from our design
- [ ] #7 Nothing on the colleague stack is enabled, flipped or configured, and the forbidden serial is never addressed by any tool of ours
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified NOT resolved, and the code itself says so out loud. bridge/executor.py:336 and docs/whatsapp.md:71,77 still state the MSISDN on L2N4C19B14054874 is UNVERIFIED and blocking; GET /v1/health reports rail.number: null, msisdn_verified: false (tests/test_bridge_executor.py::test_health_says_the_msisdn_is_unverified). This is exactly why nothing but Ivan's own test number is pinned to the rail today. Status and description remain accurate as written.
<!-- SECTION:NOTES:END -->
