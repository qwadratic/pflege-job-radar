---
id: TASK-290
title: >-
  New MCP tool: page the DB's own message/document history (replaces the
  abandoned live-screen-read design)
status: To Do
assignee: []
created_date: '2026-09-24 00:32'
labels:
  - whatsapp
  - feature
dependencies:
  - TASK-289
ordinal: 243000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24, superseding /home/claude/.claude/plans/robust-hopping-acorn.md section D (see TASK-289 for why: a live screen read cannot be trusted, since the screen and the DB were found drifted apart twice in one night). What the brain currently gets per turn is only the tail (turn_context's outbound_since_last_turn/last_outbound, app/wa/luna_brain.py:1087). Ivan wants a tool the model can call on demand to page further back -- 'вчерашний и позавчерашний' context, and to open/read attachments -- built on top of TASK-289's deleted_at filtering (a forgotten bubble or document is invisible to this tool exactly like it already is to turn_context). This is also the direct enabler for the CV-edit-assist feature (see its own task): finding and reading a candidate's existing CV means paging wa_documents for this phone.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A new MCP tool in app/wa/luna/tools_server.py (same registration pattern as show_clinic_photos) lets the model page wa_messages bubbles and wa_documents attachments for the current phone, oldest-or-newest first, with a page size and cursor
- [ ] #2 Every row the tool returns respects deleted_at is null (via app/wa/store.py's include_deleted=False default from TASK-289) -- a forgotten item never reaches the model through this tool either
- [ ] #3 A new RULES entry in app/wa/luna/prompts.py tells the model when to reach for this tool (a candidate references something from an earlier day the current tail does not cover) rather than guessing or claiming not to remember
- [ ] #4 Unit tests against the fake tools-server fixture (tests/luna_fixture_tools_server.py style) cover: pagination, a deleted_at-marked row never appearing, and an attachment read returning its stored text
<!-- AC:END -->
