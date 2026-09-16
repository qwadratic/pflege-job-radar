---
id: TASK-95
title: >-
  Store inbound WhatsApp document originals on disk, linked to the candidate in
  the DB
status: Done
assignee: []
created_date: '2026-09-14 09:44'
updated_date: '2026-09-14 13:25'
labels: []
dependencies: []
type: feature
ordinal: 95000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-14: keep the original files candidates send (CV, Urkunde, photos), not only extracted text, and connect them to the person. Today app/wa/api.py:_ingest_media downloads the media, keeps only extracted text on the card (cv_text/urkunde_text, overwritten by the next upload) and throws the bytes away; the media_id is not stored anywhere either, so a lost original cannot be re-fetched later. A single document_type/certificate_level card field is also overwritten per upload, so the harness cannot tell which document types a candidate has sent so far -- TASK-96 (CV + qualification document both required) needs that per-document record. Files are real candidate PII.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 every inbound media message (document, image, audio, video; both WA_BRAIN modes) has its original bytes written under a configurable documents directory (default data/wa_documents, env WA_DOCUMENTS_DIR) before any text extraction, so a failed extraction/classification still leaves the original stored and linked
- [x] #2 a wa_documents table row links each stored file to the candidate phone and the inbound message wamid, with media_id, mime type, original WhatsApp filename, stored path, sha256, size, received_at, and (for extracted kinds) extracted text, text key, document_type and certificate_level
- [x] #3 stored paths never derive from the untrusted WhatsApp filename (no path traversal), files/dirs are owner-only, an existing file is never overwritten, and data/wa_documents is gitignored
- [x] #4 GET /api/wa/threads?phone= returns the thread documents as metadata (no file bytes)
- [x] #5 offline tests cover storage, linkage, the failed-extraction case, the non-luna/audio path and filename sanitisation; docs/whatsapp.md documents the table, directory and PII handling
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. config.py: DOCUMENTS_DIR = env WA_DOCUMENTS_DIR or A.DATA_DIR/'wa_documents'; .gitignore data/wa_documents/.
2. store.py: wa_documents table (id, phone, wamid unique, media_id, kind, mime_type, original_filename, path, sha256, size_bytes, received_at, text, text_key, document_type, certificate_level) + idx on phone; helpers record_document -> id, set_document_text, set_document_classification, documents_for.
3. api.py: _suffix_for validates the filename extension (1-5 alnum, lowercased) else _MIME_SUFFIX (extended with WhatsApp audio/video/office types) else .bin; _write_original writes <DOCUMENTS_DIR>/<phone digits>/<UTC microsecond stamp>-<alnum media_id><ext> via mkstemp in the same dir + fsync + os.link (atomic, FileExistsError instead of overwrite), dirs chmod 0700, file 0600; _store_original downloads via media_url/download_media, writes, records the row. _handle_one: every media kind in both brains stores the original right after record_inbound (before the stopped check and before ack/extraction); inbound meta also carries media_id/mime/filename so a failed download stays re-fetchable; _ingest_media extracts from the stored blob, writes text/text_key then document_type/certificate_level onto the row, card behaviour unchanged.
4. GET /api/wa/threads?phone= adds documents (metadata from documents_for, text omitted: card slots already carry it).
5. Tests in tests/test_wa_media_intake.py (+ test_wa_harness.py fake/fixture): bytes/sha256/size/path/wamid/phone, perms, malicious filenames, extraction failure keeps file+row, deterministic + audio/video store, two docs two rows, redelivery one row, no-overwrite, threads API lists documents.
6. docs/whatsapp.md: Documents section + fix 'collects no documents' / Files bullet / POST order / threads route row.
7. Full offline suite.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented (2026-09-14):
- app/wa/config.py: DOCUMENTS_DIR (env WA_DOCUMENTS_DIR, default A.DATA_DIR/wa_documents). .gitignore: data/wa_documents/.
- app/wa/store.py: wa_documents table + idx_wa_documents_phone; record_document (commits at once), set_document_text, set_document_classification, documents_for.
- app/wa/api.py: _store_original (media_url + download_media -> _write_original -> record_document) runs in _handle_one for every media kind, both brains, right after record_inbound and before the stopped check / ack / extraction. _write_original: <dir>/<phone digits>/<UTC %Y%m%dT%H%M%S%fZ>-<media id alphanumerics><ext>; dirs chmod 0700, mkstemp 0600, fsync, os.link (atomic no-clobber, FileExistsError) instead of os.replace (which overwrites). _suffix_for now takes only a trailing 1-5 char alnum extension from the WhatsApp filename (lowercased), else _MIME_SUFFIX (extended with WhatsApp audio/video/office types), else .bin -- same helper feeds the vision temp file. _ingest_media(c, t, m, doc) extracts from the stored blob, writes text/text_key then document_type/certificate_level onto the row; card behaviour unchanged; _extract_media_text unchanged. Media inbound wa_messages.meta also carries media_id/media_mime_type/media_filename so a failed download stays re-fetchable.
- GET /api/wa/threads?phone= adds documents = documents_for minus text.
- Tests: tests/test_wa_media_intake.py (TASK-95 section + updated audio/video/deterministic/extraction-failure tests), tests/test_wa_harness.py (FakeMeta media methods, DOCUMENTS_DIR fixture).
- docs/whatsapp.md: Documents section; status line, Files bullet, POST order, threads route row, env block, TASK-81 paragraph updated.
- Smoke: synthetic data/wa_test_docs jpgs through handle_payload (deterministic, drafts, fake Meta, tmp SQLite/DOCUMENTS_DIR): files 0600, sha256 matches, '../../' filename stored as data only.

Full offline suite: 1191 passed, 126 skipped, 18 deselected (exit 0). data/wa_documents was not created by any test run. No live code path was run and data/wa.sqlite was never opened. wa_documents gets created in data/wa.sqlite the next time a process from this tree opens ST.db() (catch-up/follow-up timers); the running pflege-wa.service only picks up the new ingest code when it restarts.

Review fixes 2026-09-14 (fixer):
- vision-cli-cwd-reads-stored-originals: app/cv.py VisionClient._live_call now runs with cwd = the single-file temp dir (same dir as --add-dir) and --no-session-persistence, so no per-call ~/.claude/projects/<tmp> transcript is written. Before, the cwd was the service's repo root, which holds data/wa_documents/. cv.py comment corrected; docs PII paragraph names who can read the originals. Test: tests/test_cv_intake.py asserts cwd and the flag. Live llm vision test passed and created no project dir.
- threads-endpoint-unauthenticated-on-8502: app/wa/asgi.py calls app.auth.install(app). Test (tests/test_wa_media_intake.py): with no session, GET /api/wa/threads (also ?phone=) and /api/wa/ownership return 401; /api/wa/health and /healthz return 200; a signed webhook POST returns 200; with an owner session cookie, 200. After the next pflege-wa restart, reading threads on 8502 needs an owner session cookie from the board login (8502 has no login route). Docs updated.
- Shared with TASK-96: the ingest result is saved before the reply attempt, and catch-up skips media turns whose file is not on the card yet (see TASK-96 notes).
Offline suite: 1270 passed, 126 skipped, 26 deselected.

Repair round 1 (2026-09-14, verifier findings):
- stored-extension-from-filename (AC3 wording): api._store_original now names the original with _mime_suffix(mime_type) only (_MIME_SUFFIX, else .bin). '../../evil.sh' as application/pdf is stored .pdf, 'run.sh' with no mime .bin. _suffix_for (filename extension first) now feeds only the vision temp file. Tests: hostile-filename matrix updated (+2 cases: scan.jpeg/octet-stream -> .bin, run.sh/no mime -> .bin), _mime_suffix asserted. docs/whatsapp.md Where line updated.
- Not changed (open questions for Ivan): no retry/re-read of a stored original after a failed download/vision/classification (thread stays stuck_reply); no retention/deletion of data/wa_documents, STOP deletes nothing; pflege-wa.service still runs pre-TASK-95 code until someone restarts it.
Offline suite after repair: see TASK-96 note.

Repair round 2 (2026-09-14, final-verifier findings):
- deterministic audio/video coverage (AC5): tests/test_wa_media_intake.py::test_deterministic_brain_audio_and_video_are_stored_and_acked (audio/ogg -> .ogg, video/mp4 -> .mp4; row kind/mime/text null, bytes on disk, flat ack). Only luna audio/video had a test before.
- Not changed (open questions for Ivan): on the deterministic brain the flat media ack now needs a successful Meta download first; a failed download fails the whole webhook turn loudly (before TASK-95 the ack still went out). No retry/re-ingest from the stored original after a failed download/vision/classification: record_inbound already committed, so Meta's redelivery is a duplicate and catch-up reports media_not_ingested; the thread stays stuck_reply. No retention/deletion for data/wa_documents, STOP deletes nothing. Mixed versions until pflege-wa.service restarts: the webhook runs pre-TASK-95 code (no wa_documents rows) while the catch-up/follow-up timers load the new tree, so a luna document/image turn the old webhook fails to answer is skipped by catch-up as media_not_ingested.

Validation 2026-09-14 (after 4-lens review + adversarial verify + 2 repair rounds): full offline suite 1281 passed, 126 skipped. Live ingest smoke (real claude vision + classify, fake Meta, temp DB/dir): synthetic CV + Urkunde stored 0600 at recorded absolute paths, sha256 match, filename '../../Lebenslauf.jpg' kept as data only. Review additions kept: VisionClient runs with cwd=temp dir + --no-session-persistence; app/wa/asgi.py now installs app.auth middleware (GET /api/wa/threads and /ownership need an owner session on 8502; no login route on 8502) -- flagged to Ivan as an operational decision. NOT deployed: pflege-wa.service still runs code from 2026-09-13 20:12 UTC; restart is Ivan's call. Open: retention/deletion policy, re-ingest from stored original after failed extraction, stopped-thread downloads, Meta sha256 check, LLMClient (classify) still writes session transcripts with document text.

2026-09-14 13:25 UTC, Ivan's decision: auth middleware removed again from app/wa/asgi.py. 8502 binds 127.0.0.1 and nginx forwards only /api/wa/route-webhook, so thread reads stay open locally. The owner-gate test was replaced by test_the_standalone_harness_app_serves_thread_reads_without_a_session (AUTH_DISABLED=0; fails if the middleware is re-added, mutation-checked); docs/whatsapp.md and rollout-runbook updated. Offline suite: 1281 passed. pflege-wa.service restarted: health luna_ready=true, webhook_ready=true, autosend=true; GET /api/wa/threads?phone= returns 200 with documents=[] for the test thread. Ivan accepted as-is: consent gated by prompt only, LLMClient transcripts, and the open items (retention, re-ingest, stopped-thread downloads).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Every inbound media message is now downloaded and its original stored under WA_DOCUMENTS_DIR (default data/wa_documents, gitignored, 0700/0600, never overwritten, no filename-derived paths) before extraction, linked to phone + wamid in a new wa_documents table (media_id, mime, original filename, absolute path, sha256, size, text, text_key, classification); GET /api/wa/threads?phone= lists them as metadata. Verified by offline tests (storage, perms, traversal params, failed extraction/classification/download, redelivery, audio/video, deterministic brain), a live ingest smoke test with real vision/classification, and the full offline suite (1281 passed).
<!-- SECTION:FINAL_SUMMARY:END -->
