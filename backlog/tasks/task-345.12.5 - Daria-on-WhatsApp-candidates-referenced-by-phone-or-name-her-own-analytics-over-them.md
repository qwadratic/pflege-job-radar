---
id: TASK-345.12.5
title: >-
  Daria on WhatsApp: candidates referenced by phone or name, her own analytics
  over them
status: To Do
assignee: []
created_date: '2026-10-01 18:42'
updated_date: '2026-10-01 18:54'
labels:
  - email
  - whatsapp
dependencies: []
parent_task_id: TASK-345.12
priority: low
ordinal: 284000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-01: "я хочу чтобы у нее в будущем был вотсапп и можно было сослаться на номер или имя кандидата или устроить по ним свою аналитику". Future work, not started.

His answers, the same day: "в whatsapp, только читает по апи (по сути использует pro api, в будущем весь /про пфлеге-борд)"; "сам телефон не трогает"; "она может писать нам PII". So WhatsApp for Daria means reading candidates and their chats through the Pro API of pflege-board, read-only, later the whole /pro surface. She never sends on WhatsApp and never touches the phone rail, the bridge or the Mac mini. Her answers may name candidates (TASK-345.12.2, already changed); pipeline tasks stay by number, because the backlog is in git.

Today her board toolset excludes every phone-rail and WhatsApp-data tool (show_clinic_photos, send_updated_cv, read_history, read_document, find_stored_cv, match_cv_to_postings), and she has no Pro API access.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Daria reads candidates and their WhatsApp chats only through the Pro API, read-only; her run has no tool that sends a WhatsApp message or reaches the phone rail, verified by a test that inspects her CLI arguments
- [ ] #2 An operator can name a candidate in a mail by phone number or name, and Daria finds that candidate's record
- [ ] #3 Daria answers analytical questions over candidates (counts, stages, matches) from their records
<!-- AC:END -->
