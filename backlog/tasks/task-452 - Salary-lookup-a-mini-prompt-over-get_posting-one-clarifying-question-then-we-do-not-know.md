---
id: TASK-452
title: >-
  Salary lookup: a mini-prompt over get_posting, one clarifying question, then
  we do not know
status: To Do
assignee: []
created_date: '2026-10-07 15:17'
labels:
  - dialog
  - grounding
dependencies: []
priority: medium
project: whatsapp
ordinal: 332000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When a candidate asks about pay, a mini-prompt reads the posting text through get_posting and looks for the pay: a tariff field, TVoeD or AVR, a printed figure. If none is found, one clarifying question is asked to close the card; after two questions without progress the answer is that we do not know. Related: TASK-374 (board tools, full posting detail) and TASK-441 (provenance of every number). Requested by the WhatsApp harness lane (wa-harness) through pflege-clawl, 2026-10-07; source: Ivan 2026-10-07, notes already on TASK-314 and TASK-316 (branch docs/backlog-next-step-and-temperature). No candidate data in this text; German candidate wording only with Ivan's verbatim approval.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A posting with a tariff field, a TVoeD or AVR mention or a printed figure yields that pay with its source in the answer
- [ ] #2 A posting with none yields one clarifying question, and after two questions without progress the answer says we do not know
- [ ] #3 No pay figure is ever stated that is not in the posting text, checked by a test
<!-- AC:END -->
