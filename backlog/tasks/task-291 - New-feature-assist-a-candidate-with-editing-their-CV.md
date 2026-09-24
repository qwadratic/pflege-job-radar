---
id: TASK-291
title: 'New feature: assist a candidate with editing their CV'
status: To Do
assignee: []
created_date: '2026-09-24 00:32'
labels:
  - whatsapp
  - feature
dependencies:
  - TASK-290
ordinal: 244000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24: reduces to finding and reading the candidate's stored CV (wa_documents, document_type='lebenslauf'), generating an updated version from what the candidate says needs to change, and sending it back -- 'и легко делается' once TASK-290's history/document tool exists to locate and read the existing CV. Follows the same send-a-generated-artifact shape already proven by show_clinic_photos (app/wa/luna/tools_server.py:1085) for the send half.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A new MCP tool finds the candidate's current CV (via TASK-290's document access, filtered by document_type/deleted_at) and returns its stored text to the model
- [ ] #2 The model can produce an updated CV body from the candidate's requested changes and the existing text
- [ ] #3 A new send tool (or an extension of an existing send path) delivers the updated CV as a document to the candidate, recorded the same way any other outbound document is (wamid, wa_messages, wa_documents)
- [ ] #4 A new RULES entry in app/wa/luna/prompts.py scopes when this fires (candidate explicitly asks to change/update their CV) and what it must never do (invent qualifications or experience not in the original CV or stated by the candidate in this thread)
- [ ] #5 Tests cover: no CV on file (ask them to send one first), a CV on file gets read and an edited version sent, a forgotten CV (deleted_at set) is treated as not on file
<!-- AC:END -->
