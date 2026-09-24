---
id: TASK-291
title: 'New feature: assist a candidate with editing their CV'
status: In Progress
assignee: []
created_date: '2026-09-24 00:32'
updated_date: '2026-09-24 01:26'
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
- [x] #1 A new MCP tool finds the candidate's current CV (via TASK-290's document access, filtered by document_type/deleted_at) and returns its stored text to the model
- [ ] #2 The model can produce an updated CV body from the candidate's requested changes and the existing text
- [x] #3 A new send tool (or an extension of an existing send path) delivers the updated CV as a document to the candidate, recorded the same way any other outbound document is (wamid, wa_messages, wa_documents)
- [x] #4 A new RULES entry in app/wa/luna/prompts.py scopes when this fires (candidate explicitly asks to change/update their CV) and what it must never do (invent qualifications or experience not in the original CV or stated by the candidate in this thread)
- [x] #5 Tests cover: no CV on file (ask them to send one first), a CV on file gets read and an edited version sent, a forgotten CV (deleted_at set) is treated as not on file
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented app/wa/luna/tools_server.py::find_stored_cv() (reads wa_documents directly for document_type='lebenslauf', not the card's cached cv_text, so a forgotten CV -- TASK-289 -- is never found here even if the card still holds stale extracted text) and send_updated_cv(cv_text) (stages the model's full updated body as a .txt file on the mini via the same _stage_on_mini path show_clinic_photos uses, sends it with BR.Client().send_document, records the outbound). Deviation from AC3's literal wording: recorded via ST.record_outbound(kind='document', wamid=None) only, no wa_documents row for the OUTBOUND file -- grepped the codebase first and found send_document has no production caller anywhere yet and no outbound-wa_documents precedent exists (wa_documents is inbound-only today, keyed on Meta media_id which an outbound send never has); followed show_clinic_photos's own established precedent (record_outbound with wamid=None for a tool-sent artifact) instead of inventing a new outbound-document schema unprompted. New RULES entry CV EDIT in prompts.py. Both tools added to MCP_TOOL_NAMES. 8 new unit tests: find_stored_cv found/missing/forgotten, send_updated_cv empty-text/AUTOSEND-off/meta-rail-pinned/dry-run/real-send-records-a-row. tests/test_wa_luna_tools.py: 146/146 pass.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
CV-edit-assist built on TASK-290's plumbing: find_stored_cv() reads the candidate's actual stored CV text (skipping a forgotten one), the model writes the full updated body per the new CV EDIT rule, send_updated_cv() delivers it as a WhatsApp document and records the outbound. AC2 (the model actually produces a correct edit) is a live-model behavior, not deterministically testable in the offline lane -- left unchecked pending an llm-marked persona test or a live UAT turn; everything else verified by 8 new unit tests plus the full tools lane (146/146).
<!-- SECTION:FINAL_SUMMARY:END -->
