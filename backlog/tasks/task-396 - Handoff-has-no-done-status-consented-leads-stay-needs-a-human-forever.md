---
id: TASK-396
title: >-
  Handoff lane: Daria (email harness) closes consented WA leads and reports
  leads to Ivan and Valentyn
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
updated_date: '2026-09-30 18:23'
labels:
  - pro-api
  - email-lane
dependencies:
  - TASK-395
  - TASK-345
priority: high
type: feature
project: whatsapp
ordinal: 271000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
wa_queue_candidates.status is only ever "queued". The Pro Leads view therefore counts every consented lead as needing a human, forever. pflege-fe raised this on 2026-09-29.

**Ivan, 2026-09-30:** link the two lanes. Daria, the digital employee in the email harness (daria.s@pflege-connect.work, TASK-345), takes over consented WA leads and prepares lead reports for Ivan and Valentyn.

Shape, to agree with the email-harness session:
- **Daria reads** consented leads and their matched clinics through the WA harness token API (TASK-395 surface plus the TASK-326 queue).
- **Daria writes back** a handoff status after acting: sent to clinic, clinic answered, closed or declined. This goes through one token-gated write endpoint. It is the first write on the harness API, and Ivan approved it with this link.
- **The Pro API rows** (handoff.status) show that status, so a closed lead leaves "needs a human".
- **Daria emails lead reports** to Ivan and Valentyn, built from the same API. Cadence and format are agreed with Ivan.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The interface with the email-harness session is agreed and written into this task: what Daria reads, the write endpoint, the status values
- [ ] #2 A token-gated write endpoint records a handoff status per queue row, with an audit trail (who, when, previous status); tests included
- [ ] #3 Pro API rows expose the handoff status, and a closed handoff no longer counts as needing a human
- [ ] #4 Daria sends Ivan and Valentyn a lead report built from the API; the first one is reviewed by Ivan
<!-- AC:END -->
