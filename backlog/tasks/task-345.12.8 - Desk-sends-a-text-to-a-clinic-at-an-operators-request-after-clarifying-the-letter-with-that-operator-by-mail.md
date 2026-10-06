---
id: TASK-345.12.8
title: >-
  Desk sends a text to a clinic at an operator's request, after clarifying the
  letter with that operator by mail
status: To Do
assignee: []
created_date: '2026-10-05 09:22'
updated_date: '2026-10-06 12:51'
labels: []
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 290000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05, after Ilmtalklinik (its deputy nursing director) asked for the terms ("Konditionen") on 02.10: the parallel operator will send the terms to the sender box and Daria sends them to the clinic; if a request from the parallel operator to send something to a clinic is unclear or needs agreeing first, Daria clarifies the letter with him in the mail dialogue and then sends it. Today the desk (tools/daria_desk.py, tools/daria_tools.py) answers operators and has no tool that mails a clinic, and batch letters go only through an approved plan. Without this the terms reach a clinic only when a Claude session is open.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 An operator mail asking to send a text to a named clinic is routed to Daria as a request (not stop, skip or status) and she sends the text from the sender box to that clinic, in the thread of the clinic's latest answer when there is one, with both operators in copy
- [ ] #2 When the request is unclear, or the operator asks to agree something first, Daria answers that operator by mail, asks what is unclear, and sends once it is settled in the dialogue
- [ ] #3 Every send is written to the desk ledger with who asked and, when there was a dialogue, how it ended
- [ ] #4 tests/test_daria_desk.py covers: a request that is clear is sent in thread with both operators in copy, an unclear one gets a question first and is sent after the answer
- [ ] #5 Thread choice: when the operator names a thread (clinic, subject or date), Daria finds that thread and sends into it; when none is named she picks the thread whose content fits the text and proposes it to the operator; when no thread fits she says so ("тред не найден, могу отправить новым письмом") and sends only as a new letter if the operator says to
<!-- AC:END -->
