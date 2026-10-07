---
id: TASK-446
title: >-
  Lead status "terms sent" in our own copy of the sales CRM: record every
  commercial offer, design the frontend view for it
status: To Do
assignee: []
created_date: '2026-10-07 09:50'
labels:
  - email
  - crm
  - frontend
dependencies:
  - TASK-345.12.16
priority: medium
ordinal: 326000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-07 (voice message): when a clinic asks for the standard terms and the mailer answers with the terms letter (TASK-345.12.16, ledger event terms_sent), the system must show that a commercial offer was sent to that clinic. We already hold a copy of the sales CRM ("Sales Brain") of our own, so far in test mode; Ivan wants a lead-status column in that copy and a frontend that shows it. For now this is a task only: design first, no build. Where exactly the copy lives and what its schema is must be confirmed with Ivan in the first step (the live CRM is the colleague's SQLite file and stays read-only for us).

Intent:
- A lead-status column in our own copy (not in the colleague's database). One value per clinic lead, set from our own ledgers (the mailer ledger events such as terms_sent, terms_blocked, a clinic's written yes), idempotent, with the event it came from, the time and the recipient, so it can be rebuilt from the ledger at any time.
- A frontend view designed for that status: which clinics got the offer, when, in which thread, what is the next step. Design only: sketch, fields, filters.
- Old CRM stage labels stay out of any decision code (Ivan, 2026-10-06); the new status is written by us for display and follow-up, and nothing that decides whom to write reads the old stages.

First deliverable (design and inventory, no code): where the copy is and how it is filled, its schema and the test-mode state; the status vocabulary (for example none, terms sent, terms blocked, accepted, declined) and which ledger event sets which value; the column and its migration; a frontend sketch; open questions for Ivan.

Relations: builds on TASK-345.12.16 (terms letter); see also TASK-316 (lead status of WhatsApp leads, a different lead kind) and TASK-443 (clinic contact registry). Requested by the mailer lane (pflege-board-25) through pflege-clawl, 2026-10-07. No person's name, clinic name, price or address in this public text.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Inventory of the existing copy: location, schema, how it is refreshed, what test mode means, written down without real paths, names or contact data (the repo is public)
- [ ] #2 Status vocabulary and the mapping from ledger events to status values, with a rule for conflicts and for a rebuild from the ledger
- [ ] #3 The column and its migration designed on the copy, nothing written to the live CRM
- [ ] #4 Frontend view sketched (fields, filters, empty state) and agreed with Ivan
- [ ] #5 Follow-up build tasks split from the design, created on origin/main
<!-- AC:END -->
