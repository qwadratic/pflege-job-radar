---
id: TASK-439
title: >-
  WhatsApp status message: one good clinic plus the code-appended link to her
  status page
status: To Do
assignee:
  - wa-harness
created_date: '2026-10-06 08:55'
labels:
  - whatsapp
  - luna
dependencies: []
priority: medium
ordinal: 315000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-06, relayed by pflege-fe: the WhatsApp message to a pooled candidate carries one good clinic as an example, plus a line "here you can see where we sent your profile" with the status-page link (TASK-436). Everything else she asks in the chat. The German line is fixed candidate-facing text and needs Ivan's verbatim approval. The link is appended by code. It cannot be sent while the WhatsApp rail is down (handset lent out since 10-02). Requested by the WhatsApp lane (wa-harness).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ivan approved the German line verbatim, and it is stored as a constant
- [ ] #2 The message picks one clinic by a stated rule and appends the link in code; covered by offline tests
- [ ] #3 A dry run on a test thread shows the exact bubbles; no live send without Ivan
<!-- AC:END -->
