---
id: TASK-139
title: >-
  Correct the shipped WhatsApp docs and rewrite plan section 5.8 for Ubuntu
  systemd
status: To Do
assignee: []
created_date: '2026-09-21 09:10'
updated_date: '2026-09-21 09:20'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-21-macmini-revision.md
documentation:
  - docs/whatsapp.md
priority: high
type: docs
ordinal: 147000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Our own documentation is wrong in production-visible ways, and it was shipped. Four errors, all established read-only on 2026-09-21 (/home/claude/plans/2026-09-21-macmini-revision.md):

1. The rail table delivery-status column says no. The phone rail DOES yield a per-message delivery state: their whatsapp.py:27 scrapes the bubble status content-desc as one of Gesendet, Zugestellt, Gelesen, and it is read at send time, not pushed. There is no webhook and no Meta status code -- but "no delivery status" is false.
2. Both "+49" claims about the sender number. The only identity ever captured on either handset is +48, profile name "Babu22", and it belongs to the farm phone we cannot have. The number on the lane handset is unrecorded (TASK-136).
3. The handset name. huawei_p30_lite_02 is the ChatGPT farm phone; the lane is on _01.
4. The macOS assumptions. The machine is Ubuntu 24.04.5 on Apple hardware; launchd, LaunchDaemon, .plist and pmset are void, and the primitive is systemd --user plus loginctl enable-linger, already proven in that account without sudo by the existing reverse-tunnel unit.

Plus the id-space and invariant change from decision-8: there is no provider message id on this rail at all, the client_msg_id we mint is the only id an outbound message has, and "no sent without a confirmed provider message id" becomes "no sent without a verified delivery tick" on this rail only -- while app/wa/meta.py:560-563 keeps its form.

Keep the Transports section existing voice and its PLANNED versus EXISTS discipline. Verify each claim against the tree or against a read-only ssh read before editing it; the errors being corrected here were all confident sentences written from an unverified source.

Scope note: the plan file lives outside the repo at /home/claude/plans/2026-09-20-wa-home-transport-plan.md. Its section 5.8 still describes two LaunchDaemons, pmset autorestart and disabling sleep.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The rail table delivery column states that the phone rail yields per-message ticks scraped at send time, with no webhook and no Meta status codes
- [x] #2 Every +49 claim about the phone-rail sender is removed; the doc states the captured identity was +48 on the farm handset and that the lane handset number is unverified and blocking
- [x] #3 The handset is named huawei_p30_lite_01 with its serial, and the forbidden serial is named as the ChatGPT farm phone
- [x] #4 The macOS wording is gone from the doc: the machine is described as a Ubuntu box and supervision as systemd user units with linger
- [x] #5 The invariants section states the tick-for-id renegotiation as rail-scoped, and states that meta.py keeps its form on the Meta rail
- [ ] #6 Plan section 5.8 is rewritten for Ubuntu systemd user units and the single VPS-initiated -R leg, with the pmset question restated as an Apple EFI question for a human at the site
- [ ] #7 Every corrected claim was checked against the tree or a read-only remote read before it was written, and the check is named in the notes
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Doc half landed 2026-09-21 in docs/whatsapp.md, Transports section (TASK-116). Plan section 5.8 rewrite (AC 6) is NOT done: that file lives outside the repo and was out of lane for this session.

Lines changed in docs/whatsapp.md:
- 60 "Planned, not built: a second rail" -- rewritten: Ubuntu box macmini-worker1 24.04.5 on Apple hardware rather than a machine at Ivan home; handset huawei_p30_lite_01 serial L2N4C19B14054874; _02 / L2N4C19B14054035 named as the off-limits ChatGPT farm phone; sender number called UNVERIFIED and blocking; systemd --user plus linger rather than launchd; the executor wraps an existing live lane rather than being built from scratch.
- 65 rail table, bridge row -- sender cell, wire cell (HTTP to a Ubuntu box, adb to the handset) and delivery cell (no / no / ticks, read at send time).
- 67 NEW paragraph "The delivery column is ticks, not statuses" -- what the tick is, that it is scraped at send time, that there is no webhook and no id to correlate it to, and that an unreadable tick is a 504.
- 69 "The sender number" -- was "no new SIM" and reused a +49; now states both halves were wrong, that the only captured identity was +48 "Babu22" on the farm handset, that the _01 number is recorded nowhere, the two read-only settles, the blocking rule, and that the SIM question is re-opened.
- 71 "Why (Ivan)" -- "no delivery/read statuses" corrected to "no pushed delivery statuses, only the tick read once at send time"; added Ivan 2026-09-21 re-confirmation that cold first contact stays on the phone rail plus its two gates (TASK-113/TASK-137 before any first touch; pacing at the handset-side constants, TASK-127).
- 79-83 route table -- direction column "home" replaced by "executor"; health row "session linked?" to "rail usable?".
- 85-99 the representative send -- 200 body drops provider_msg_id and carries verified.tick; trace carries bubble_index; added the paragraph stating 504 for a missing tick and 202 as the common answer under a paced rail.
- 105 invariant bullet -- split into the two rail forms: Meta keeps confirmed provider message id and meta.py:560-563 keeps its form; the phone rail gets no sent without a verified delivery tick, and unverified may never become sent.
- 106 NEW bullet "The id space is ours on this rail" -- client_msg_id is the only id, nothing to correlate afterwards, indeterminate goes to a human not to a resend.
- 107 idempotency bullet -- key source is "a Meta wamid on one rail, the inbound id we mint on the other"; bubble_index made first-class.
- 108 wamid UNIQUE bullet -- added why the existing lane fingerprint is not collision-free and what TASK-131 replaces it with.
- 109 opt-out bullet -- blocking scope widened from "before any campaign on the bridge" to "any campaign on either rail", now naming TASK-113 and TASK-137.

Verification before writing, not after. Read-only over ssh on the remote box: farm_usb.py lines 50-60 (the name-to-serial comment and DEFAULT_SERIAL), apps/wa_phone/config.py lines 1-49 (HUAWEI_01_SERIAL, FORBIDDEN_SERIALS, PACING), grep of whatsapp.py for the status content-desc and the unverified path, grep of the package for any message id. In-tree: meta.py:562 (the one-lie comment), store.py:42 (wamid text unique), transport.py:36-40 (the bridge branch raises), campaign.py 653-659 (the 4xx classification). Offline suite after the edits: 1710 passed, 127 skipped, 70 deselected.

AC 7 left unchecked on purpose: it spans both halves and the plan-file half has not been written yet.
<!-- SECTION:NOTES:END -->
