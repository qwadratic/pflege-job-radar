---
id: TASK-345.11.4
title: >-
  One planned chain per clinic: first letter plus two follow-ups; switching it
  on needs no confirmation and puts all three sends into the send calendar
status: To Do
assignee: []
created_date: '2026-10-08 06:49'
labels:
  - email
dependencies: []
parent_task_id: TASK-345.11
priority: high
type: feature
ordinal: 335000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-08: "отправка и два фоллоуапа одной спланированной цепочкой. Включение этой цепочки должно работать без подтверждения и автоматически включать в «календарь» эти отправки." Context: an unannounced one-letter send (classic batch) and its follow-ups are separate today: the first letter of one clinic went 2026-10-08 08:10 and its fu1 (Tue 2026-10-13) and fu2 are in no batch; planning them needs another `plan --announce-at` and another sender process in tmux; every batch needs its own long-lived sender, and a dead sender loses its slots (2026-10-07: five wave-2 fu1 missed because the 2026-10-02 batches had no sender).

Wanted: (1) a chain = initial + fu1 + fu2 for a recipient, planned together when the first letter is switched on; (2) switching on is one action with no extra confirmation (a wave's approval covers its follow-ups, Ivan 2026-10-05); (3) the chain's sends enter the "calendar" automatically: a durable schedule of due sends that one runner reads (also Ivan 2026-10-07: one runner and a queue instead of one sender per batch); the calendar also lists every upcoming send; (4) the ledger stays the source of truth (a send is idempotent per recipient and step); a missed slot is recorded and loud, no silent send-late, no invented cap or fallback (No safety nets); reply/bounce/complaint/opt-out stop rules unchanged.

Open design question for Ivan: at a missed slot, send at once in the same working window, or record and re-plan. Related: TASK-345.11.1, TASK-345.12.15, TASK-345.12.1. Requested by the mailer lane (pflege-board-25) through pflege-clawl on Ivan's word of 2026-10-08. No person names in this text.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Acceptance criteria are written together with the design and agreed with Ivan, including the answer to the missed-slot question
<!-- AC:END -->
