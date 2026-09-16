---
id: TASK-102
title: >-
  Import campaign recipients history from the old system; ask before reusing
  their earlier CV/Urkunde
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 14:24'
updated_date: '2026-09-14 21:23'
labels: []
dependencies:
  - TASK-100
type: feature
ordinal: 102000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Campaign recipients (Ivan 2026-09-14: old candidates, list supplied by a colleague) already told the old bot their qualification, city, housing needs, sent CVs/Urkunden, and some were submitted to clinics. Our harness sees none of it: Luna would re-ask facts, and nothing tells it which documents we already hold. Ivan asked that nothing is lost. Source is the old system on the same host (sales_brain.sqlite, read-only; attachment originals on disk). Ivan authorized reading the old bot data 2026-09-14.

CHANGE (Ivan, 2026-09-14, SUPERSEDES any earlier instruction that imported documents satisfy the documents gate directly): if we already hold this person's CV and/or Urkunde from the old system, Luna must always ask whether we may use those documents or whether they want to send new ones. Imported documents are stored and linked, but count for the TASK-96 gate only after the candidate confirms reuse; if they want to send new ones, the gate waits for the new uploads as usual. Ask as a plain yes/no (TASK-97), e.g. may we use the CV and Urkunde you sent earlier, with the note that they can simply send newer ones here.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 for each recipient phone the importer reads the old system read-only and seeds the card with known facts (region, city, qualification path, housing, placement/submission status) plus a short prior-contact summary the model can use, reporting per phone what was imported and what was absent
- [x] #2 document originals the candidate sent to the old bot are copied into wa_documents storage (same perms/linkage as TASK-95, source recorded, classified with our own pipeline) and appear on the card marked as imported and not yet confirmed for reuse
- [x] #3 an imported, unconfirmed document never satisfies the documents gate; next_objective and the prompt make Luna ask as a plain yes/no whether the earlier CV/Urkunde may be used (naming which ones we hold), noting they can send newer ones instead
- [x] #4 a yes marks exactly the imported documents it refers to as reuse-confirmed and they then count for the gate; a no or a new upload leaves them unconfirmed and the gate follows normal TASK-96 rules for the new files; the decision is recorded on the card and the wa_documents rows
- [x] #5 the importer never writes to the old system, is idempotent on re-run, fails loudly on unreadable sources, and runs in dry-run by default
- [x] #6 offline tests use a synthetic fixture with the old schema and cover confirm-reuse, decline-reuse-then-upload, and partial holdings (CV only); an llm persona run shows the reuse question and both answers; docs state what is imported and how reuse is confirmed
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. store.py: wa_documents gains import_source, import_ref, import_meta (JSON: origin, source system, old class/type, sent_at, source path, obtained disk|meta, expected sha256), reuse_state (pending|confirmed|declined, null for WhatsApp uploads), reuse_decided_at; column migration in db() (pragma table_info + alter table add column, same as app/runs.py) + unique index (import_source, import_ref). New table wa_imported_messages (prior history, unique per source ref; never wa_messages, so ball/follow-ups/outbound_since_last_turn/window are untouched). Helpers: record_imported_document, imported_document, document_with_sha256, set_document_reuse, record_imported_message(s), imported_messages_for.
2. api.py: factor _read_and_classify out of _ingest_media (same extraction + classify_document + row writes) for the importer; process_owed_turn applies the model's document_reuse decisions to wa_documents rows and appends a confirmed document's stored text to cv_text/urkunde_text; GET /wa/threads?phone= adds imported_messages.
3. luna_brain.py: gate counts an imported document only when reuse=confirmed; next_objective asks one plain yes/no whether the earlier CV/Urkunde (named, ids) may be used, noting newer ones can be sent, before asking for the missing ones; OUTPUT_SCHEMA document_reuse {confirmed_ids, declined_ids}; turn() validates ids (imported entries on the card, raise otherwise), sets card.documents[].reuse/reuse_decided_at; documents, prior_contact, prior_placement code-owned (stripped from card_patch).
4. prompts.py: PRIOR CONTACT and EARLIER DOCUMENTS rules; DOCUMENT ASK/OUTPUT_INSTRUCTION updated.
5. app/wa/luna/import_history.py: operator-supplied SQL file with four named queries (facts, placement, messages, documents; documented column contracts, :phone/:phone_digits bound), source opened mode=ro, media roots for relative paths; per phone import_phone() (TASK-103 calls it) -> report; dry-run default (no writes, no LLM, no Meta), --apply: facts fill only empty card keys (conflicts reported), prior_contact deterministic summary + prior_placement, history rows, documents copied with api._write_original + wa_documents import row + sha256 verified + our extraction/classification; candidate/forwarded originals before derived CVs (derived imported only when no lebenslauf held); missing file + media_id -> Meta re-fetch on apply, else unrecoverable reported; skip rows reported; unreadable DB/root/file raises naming path and the grant; idempotent by source ref and sha256; card merge under ST._lock with a phone claim.
6. deploy/import-history.example.sql for the old system (written from old-system code only).
7. Tests: synthetic fixture DB (old schema) + synthetic files; dry-run writes nothing; apply seeds card/docs; idempotency; unreadable source/file; missing + Meta re-fetch/unrecoverable; migration of an old wa_documents table; gate/objective/turn: confirm-reuse, decline-reuse-then-upload, CV-only partial holdings. llm persona run: reuse question + yes + no.
8. docs/whatsapp.md section + rollout-runbook ACL prerequisite (applied, revert command). Offline suite.

Fixer round (review 2026-09-14): import report lists inbound Stopp messages of the imported history (SL.is_stop), dry-run and apply; the campaign sender skips such phones (skip_opted_out). read_and_classify records no readable text as document_type unreadable instead of raising.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
VERIFIED FACTS for implementation (2026-09-14, checked on this host; supersede any guess):
- Source DB /opt/clinic-dispatcher/var/sales_brain.sqlite (WAL): the service user claude CAN open it directly with sqlite URI mode=ro (verified: select count(*) from candidates -> 140). No sudo needed for the DB.
- candidate_attachments.storage_path resolution, same as the old system (apps/manager_crm/service.py:resolve_media_file, apps/connectors/candidate_whatsapp_media.py:resolve_under_media_root): a RELATIVE path lives under one of the media roots /opt/clinic-dispatcher/data/private/candidate_whatsapp_media or /opt/clinic-dispatcher-v2-bridge/data/private/candidate_whatsapp_media (try both, reject .. and absolute); an ABSOLUTE path is used as is (standardized CVs under /opt/clinic-dispatcher/data/private/manager_crm_lebenslauf, a few clinic packets).
- source_system meaning: meta_whatsapp_cloud (161 rows) and telegram_bot (9) = originals the candidate sent; owner_telegram / owner_telegram_pdf = files an operator forwarded; manager_crm_standardized_cv (524) = CV re-generated by the old CRM from the candidate CV (import as a CV with that source recorded; prefer the candidate original when both exist); clinic_inbound_packet / manager_crm_inbound_clinic_packet = clinic-side packets, not candidate documents (skip, report).
- On-disk availability today (counted as root): meta_whatsapp_cloud 66 under /opt/clinic-dispatcher root + 12 under the v2-bridge root + 83 missing; telegram_bot 9 missing; manager_crm_standardized_cv 104 present, 420 missing. For a missing WhatsApp original, fetch it again from Meta by candidate_whatsapp_messages.media_id (same phone, same wamid/attachment link) with our Meta client; if Meta no longer has it, report it per phone as unrecoverable -- never silently skip.
- Access: /opt/clinic-dispatcher/data/private is drwxr-x--- dispatcher:dispatcher, so claude cannot read the file stores today. Prerequisite (one-time, operator runs, documented in the runbook with the exact commands and how to revert): read-only ACL for user claude -- traverse (--x) on /opt/clinic-dispatcher/data/private and /opt/clinic-dispatcher-v2-bridge/data/private (and their parents if needed), recursive rX plus default rX on the two candidate_whatsapp_media roots and manager_crm_lebenslauf. Code runs as claude, never sudo; an unreadable path fails loudly naming the path and the missing grant.

ACCESS PREREQUISITE DONE (Ivan ran it 2026-09-14): read-only ACL for user claude on /opt/clinic-dispatcher/data/private (--x), /opt/clinic-dispatcher-v2-bridge{,/data,/data/private} (--x), and recursive + default rX on /opt/clinic-dispatcher/data/private/candidate_whatsapp_media, /opt/clinic-dispatcher/data/private/manager_crm_lebenslauf, /opt/clinic-dispatcher-v2-bridge/data/private/candidate_whatsapp_media. Verified as claude: 438 files readable, 0 access errors. The importer runs as claude with no sudo; document these exact commands (and revert: setfacl -R -x u:claude <dirs>) in the runbook as already applied on this host.

2026-09-14 slice 1 (implemented, not finalized):
- store.py: wa_documents + import_source/import_ref/import_meta/reuse_state/reuse_decided_at (CREATE TABLE for new files, _migrate() alter table add column for existing ones, duplicate-column race tolerated) + unique index (import_source, import_ref); table wa_imported_messages; helpers record_imported_document, imported_document, document_with_sha256, document_by_id, set_document_reuse, record_imported_message, imported_messages_for.
- api.py: read_and_classify() factored out of _ingest_media (same functions); process_owed_turn calls LB.apply_document_reuse after the send; GET /wa/threads?phone= adds imported_messages.
- luna_brain.py: _counts_for_gate (imported counts only with reuse=confirmed), _reuse_pending, _reuse_objective (one yes/no naming held docs + ids, names a not-held doc as still needed); OUTPUT_SCHEMA document_reuse; turn() -> _decide_document_reuse (ids must be imported card entries, raise otherwise); apply_document_reuse writes rows and appends/removes the stored text on cv_text/urkunde_text; documents/prior_contact/prior_placement code-owned.
- prompts.py: PRIOR CONTACT, EARLIER DOCUMENTS, DOCUMENT ASK/OUTPUT_INSTRUCTION pointers.
- app/wa/luna/import_history.py + deploy/import-history.example.sql (old-system queries from code only).
- tests/test_wa_luna_import_history.py: 30 offline tests. WhatsApp subset 527 passed.
Decision: history goes to wa_imported_messages + card.prior_contact summary, never wa_messages (would change ball/follow-ups/outbound_since_last_turn/has_inbound). Imported text reaches cv_text/urkunde_text only on confirmation.

2026-09-14 slice 2 (implemented, not finalized):
- Prompt EARLIER DOCUMENTS: the reuse yes/no is asked whenever documents are the next step (also in the turn that settles the last other gate, when next_objective still shows that gate), by type, instead of asking for new files; ids from card.documents.
- luna_brain: _reuse_objective names held documents by their own type (_HELD_NAMES), a needed document not held by the path's name. Test for defizit path + imported Urkunde.
- import_history: Meta errors without an HTTP status (no token, network) raise; an HTTP refusal is not_recoverable. Dry-run notes that a derived CV is imported on --apply only when no lebenslauf is held. prior_contact.summary carries no reuse state (card.documents is the live one).
- tests/test_wa_luna_import_reuse_personas.py (llm): imported history (facts, CV, Urkunde) + campaign + Ja -> city -> reuse question -> yes / no + new CV.
- docs/whatsapp.md section 'Campaign recipients: imported history, reuse of earlier documents (TASK-102)', documents table columns, gate paragraph; docs/rollout-runbook.md step 6 (grants as applied, revert, dry-run/apply commands).
Verification: offline tests/test_wa_luna_import_history.py 31 passed; each key test failed under a throwaway mutation (gate always counts, no migration, apply_document_reuse no-op, dry-run opening ST.db, no source-ref lookup, no sha256 check, facts overwrite, no reuse objective). Full offline suite 1405 passed, 126 skipped, 51 deselected. llm: test_wa_luna_import_reuse_personas.py 4/4 (yes x2, no x2; reuse question 'Sie hatten uns ja im Mai schon Ihren Lebenslauf und Ihre Urkunde geschickt. Dürfen wir diese Unterlagen verwenden? Gern können Sie uns hier auch neuere schicken.'; Urkunde/housing never re-asked). Regression llm: test_wa_luna_personas.py close_sequence + 2 svetlana + anna_defizit 4/4; campaign full funnel [1] + typed Ja [1] 2/2. Production timers (catch-up 17:05, follow-ups 17:08 UTC) ran the migrated ST.db() with Result=success. Not run against the real old DB or files (hard rule for this session).

Late fix: a phone no query returns a row for is reported found=false and nothing is written (no thread, no prior_contact saying 'no messages on record'). Test added; import tests 32 passed; WhatsApp subset re-run.

Fixer 2026-09-14 (review F3 old-system opt-outs): import_phone reports stop_messages (inbound history messages matching slots.is_stop, {source_ref, at, body}) in dry-run and apply (import_history.stop_messages). read_and_classify (shared) now records a file without legible text as document_type unreadable instead of raising, so one old photo no longer fails a phone's import. Test tests/test_wa_luna_import_history.py test_a_stopp_in_the_imported_history_is_reported_in_dry_run_and_apply (a 'Stopfen' message does not count). Docs: import report paragraph.

Final verification 2026-09-14 20:45-20:57 UTC (after 4-lens review, adversarial verify, fixer + 2 repair rounds): offline suite 1474 passed, 126 skipped, 0 failed. pflege-wa.service restarted 21:22 UTC on this tree; health webhook_ready/outbound_ready/luna_ready true, threads 200. Nothing sent; campaign not run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
An importer reads the old system read-only (operator SQL in deploy/import-history.example.sql) and seeds facts, placement status and a prior-contact summary; candidate document originals are copied into wa_documents and counted only after the candidate confirms reuse, otherwise the gate waits for new uploads. Dry-run default, idempotent, loud on unreadable sources; read ACL for claude applied by Ivan (438 files readable). Verified on a synthetic old-schema fixture and live llm reuse personas 4/4; not yet run against the real old DB.
<!-- SECTION:FINAL_SUMMARY:END -->
