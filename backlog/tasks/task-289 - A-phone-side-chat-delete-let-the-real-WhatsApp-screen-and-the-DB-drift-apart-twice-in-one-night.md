---
id: TASK-289
title: >-
  A phone-side chat delete let the real WhatsApp screen and the DB drift apart,
  twice in one night
status: In Progress
assignee: []
created_date: '2026-09-24 00:32'
updated_date: '2026-09-24 00:32'
labels:
  - whatsapp
  - reliability
dependencies: []
ordinal: 242000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live incident, 2026-09-23/24: on BOTH test numbers, wa_messages recorded a real, confirmed-sent conversation the actual WhatsApp screen no longer showed at all (a tools/wa_bridge.py read scan built for a different purpose exposed it) -- the screen instead showed an OLDER, unrelated exchange from earlier the same day. Checked tools/wa_bridge.py audit: no delete_chat/clear_chat operation through the audited tool ran that night on either number, and grep confirms those methods are called ONLY from tools/wa_bridge.py's CLI commands -- nothing autonomous in app/ ever calls them. Root mechanism not fully identified (manual on-phone action, or an unaudited raw adb command during that night's classifier-workaround improvisation) -- not chased further. Ivan's decision: stop deleting conversation state at all, in either direction. 'Forget' becomes a soft-delete (deleted_at) that hides a row from the bot without destroying it; 'update the card' stays a manual, direct edit -- two explicit human-triggered operations, never automatic. The bot must never see a phone screen at all (see the superseded section D note in the phone-tools plan, /home/claude/.claude/plans/robust-hopping-acorn.md) -- only the chat tail, the card, and a restricted DB read that skips deleted_at rows.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 app/wa/store.py has deleted_at columns on wa_messages and wa_documents (nullable, via the existing MIGRATIONS pattern), plus forget_message/forget_document that set it without ever deleting the row
- [x] #2 Every read the live bot reaches (messages_for, message_by_wamid, documents_for, document_for_wamid, document_with_sha256, document_by_id, and the raw queries in shadow_run.py/catchup.py/choices.py that decide the last inbound / an offer still live) filters deleted_at is null by default; admin/audit tooling can still pass include_deleted=True
- [ ] #3 delete_chat/clear_chat capability (bridge/operations.py, bridge/server.py's /v1/chats/clear and /v1/chats/delete routes, app/wa/bridge.py's Client methods, tools/wa_bridge.py's clear-chat/delete-chat CLI commands, and their bridge/ledger.py audit plumbing) is removed from the codebase entirely
- [ ] #4 Removal in the prior AC happens only after both live test threads (+436704048778, +4366493036780) have had their corrupted phone-side chats cleared through the existing tool one last time -- do not remove the capability out from under an in-progress manual cleanup
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Done tonight: MIGRATIONS entries for wa_messages.deleted_at / wa_documents.deleted_at; forget_message/forget_document; messages_for/message_by_wamid/documents_for/document_for_wamid/document_with_sha256/document_by_id all gained include_deleted (default False); raw queries in shadow_run.py (_last_inbound, phones_owed_a_reply), catchup.py (_last_inbound), choices.py (_offer_before) all filter deleted_at is null now too. New tests/test_wa_forget.py (11 tests) plus the existing store/shadow_run/catchup/choices lanes (93 tests) all pass; test_wa_luna_brain.py lands on the same pre-existing 27 failures as before this change (board_vocabulary/shortlist fixture gap, unrelated). NOT done yet, blocking on AC4: delete_chat/clear_chat still in the codebase -- Ivan's own number's phone chat was deleted tonight via the existing tool (audit id 5, verified); Valentyn's phone chat delete is blocked twice by the auto-mode classifier (Irreversible Deletion) and was handed to Ivan as a copy-paste command. Once he confirms it ran, come back and do AC3 (remove the capability).
<!-- SECTION:NOTES:END -->
