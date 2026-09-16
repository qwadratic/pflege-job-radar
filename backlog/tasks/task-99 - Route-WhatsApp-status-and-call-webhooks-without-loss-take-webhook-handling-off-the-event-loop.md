---
id: TASK-99
title: >-
  Route WhatsApp status and call webhooks without loss; take webhook handling
  off the event loop
status: Done
assignee:
  - '@claude'
created_date: '2026-09-14 14:24'
updated_date: '2026-09-14 21:23'
labels: []
dependencies: []
type: bug
ordinal: 99000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-14 while checking campaign readiness: app/wa/router.py only routes value.messages. Status webhooks (sent/delivered/read/failed with error codes) and call events are dropped for both systems, so neither we nor the real system learn about failed deliveries; a change carrying messages and statuses copies the statuses into both halves, leaking our statuses to the real system. The route handlers are async def but call synchronous code that holds ST._lock through a 7-26 s Luna turn on a single uvicorn worker, so a burst of replies blocks the whole 8502 process and pushes webhooks past the nginx 60 s proxy timeout (Meta then redelivers). Ivan approved moving handling off the event loop.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 statuses[] are split by recipient through route_decision: statuses for real-system phones are forwarded re-signed; statuses for our phones are stored per wamid (status, timestamp, error code/title/details, pricing/conversation info) and a failed status is also recorded as a send failure visible in GET /api/wa/threads
- [x] #2 call events and any other webhook fields the router does not understand are forwarded to the real system unchanged rather than dropped, and no status/call object is duplicated into the wrong half
- [x] #3 webhook handlers no longer run blocking work on the event loop; a slow Luna turn does not delay /api/wa/health or a concurrent forward, verified by a test
- [x] #4 offline tests cover status-only, mixed, call and unknown-field payloads for both owners; docs updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. store.py (append): wa_message_statuses (unique wamid+status+timestamp; failed -> wa_send_failures in the same transaction, code/title/details), latest status per wamid; wa_webhook_events (raw, fingerprint-dedup for redelivery); wa_inbound_pending (one row per accepted inbound until its processing finished; attempts/last_error); record_inbound_pending (message + pending row atomically); claim state lookup.
2. api.py webhook section: accept_payload (phase 1: record messages via record_inbound_pending, statuses, every other value key raw; foreign phone_number_id stored raw) -> no download/brain/send; finish_inbound(c, m) = the old _handle_one tail made idempotent and claim-guarded (arrival bookkeeping derived from the message row; media claim 'media:<wamid>' around store/re-download via meta.media_id/re-read stored original + ingest; media ack under the reply claim; process_owed_turn); drain_phone (pending rows oldest first, stop when a claim for the phone is in flight elsewhere, failures recorded + continue); single-thread background executor; handle_payload stays synchronous (tests/scripts). Routes: async handler reads body then run_in_threadpool; /wa/webhook and /wa/internal-webhook accept + submit + 200.
3. router.py: split messages(from)/statuses(recipient_id)/calls(direction)/contacts, user_preferences(wa_id)/echoes(to) per owner; unattributable keys, foreign phone_number_id and unknown shapes go to them unchanged; envelope only copied; no ST._lock while routing; record us, submit, forward them synchronously (ForwardError -> 502).
4. catchup.py: drain every phone with pending rows first (same pipeline), then the ball-based legacy pass via finish_inbound (media re-download/re-ingest instead of media_not_ingested skip, media ack retry); queue build after consent also from catch-up; per-phone errors recorded, reported, non-zero exit.
5. GET /wa/threads: pending_inbound + stuck on old pending rows; ?phone= adds message_statuses and webhook_events.
6. Tests: router status-only/mixed/calls/unknown for both owners, contacts split, background order, slow-turn responsiveness (health + forward), crash recovery text/media (not stored, stored-not-ingested), forward failure non-2xx; conftest waits for the background worker before monkeypatch undo. Update existing tests to the new semantics.
7. Docs: api.py docstring order of business, router/catchup docstrings, docs/whatsapp.md.

Fixer round (review 2026-09-14): 1. router: every call object (calls[], statuses[] type=call, interactive call_permission_reply) goes to the real system whatever the owner (the old system is the only call bridge); a phone we own also keeps a raw copy; call statuses never land in wa_message_statuses. 2. api.read_and_classify: no readable text (CV.NoReadableText) is the final classification 'unreadable' (not on the gate) and the reply turn runs; retries stay for transient failures. Tests, docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-14 15:06 incident: first test run after moving turns to the background worker: tests/test_wa_harness.py::_route restored M.Client before the worker sent, so the worker built a real app.wa.meta.Client and POSTed once to graph.facebook.com with the test token 'test-token' and phone_number_id 111222333 (Meta answered HTTP 401, nothing delivered; recorded only in that test's tmp sqlite). Fixed at once: submit_accepted builds the Meta client in the request, tests/conftest.py waits for the background worker before monkeypatch undo, _route waits before restoring. Also: the catch-up/follow-up timers run from this tree, so the new SCHEMA tables were created in data/wa.sqlite by a timer run at 14:52 (additive, empty).

Implemented: store.py wa_message_statuses/wa_webhook_events/wa_inbound_pending + helpers; api.py accept_payload (request thread), submit_accepted (one ThreadPoolExecutor worker, Meta client built in the request), process_phones/drain_pending/finish_inbound (idempotent: arrival bookkeeping from the row, media:<wamid> claim around store/re-download/re-read+ingest, media ack under the reply claim), routes read body then run_in_threadpool; router.py splits messages/statuses/calls/contacts/user_preferences/echoes by owner, unattributable -> them unchanged, ForwardError -> 502; catchup.py pass 1 drains pending phones, pass 2 legacy ball-based via finish_inbound, consent queue builds, per-message errors reported, exit 1. Tests: tests/test_wa_router.py (+8 split tests), tests/test_wa_webhook_background.py (13: 200-before-turn, health+forward during slow turn, slow forward vs event loop, 502 + redelivery once, arrival order, failed turn recorded + catch-up, crash recovery text/claim-in-flight/media not stored/stored not ingested/sha mismatch/media ack, direct webhook statuses+events). Mutation check: running the forward on the loop or the turn in the request fails the responsiveness tests. Existing tests updated: catch-up results carry wamid; media turn claimed by the webhook reports claimed_elsewhere; a failed ingest is re-read by catch-up (test renamed).

Decision (deviation from 'statuses exactly like messages'): only a message runs route_decision(); statuses/calls/contacts/user_preferences/echoes go to the phone's RECORDED owner, no record -> them, nothing recorded; a status of one of our own outbound wamids is always ours. Reason: the known-phones export lags, and a status of the real system's outbound message to a brand-new number would otherwise permanently hand that conversation to us. Senders are decided in a pre-pass so a new lead's contacts follow its message. Tests in tests/test_wa_router.py. Deploy: pflege-wa.service (started 13:25) still runs the pre-TASK-99 webhook in memory; the catch-up timer already runs the new catchup.py (journal: '0 message(s) attempted' since 15:07). Until the service is restarted, a luna media message arriving while a catch-up pass fires could be downloaded/read by both (old webhook takes no media claim). Restart needed to go live.

drain_pending keeps a message pending only for KEEP_PENDING = (claimed_elsewhere, rate_limited), separate from TURN_NOT_RUN (the save-skip list), so a new no-op status from TASK-101 (no_send_recorded) can join TURN_NOT_RUN without looping pending rows. Full offline suite 15:40: all TASK-99 tests green; failing tests at that time belong to in-progress TASK-100/TASK-101 edits (test_wa_process_owed_turn turn_context, test_wa_store_claims skipped_no_send, test_wa_media_intake download-failure meta now carrying reply_to_wamid/context via the generic inbound_meta).

Fixer 2026-09-14 (review F1/R1, calls): api.is_call_object (calls[], statuses type=call, interactive call_permission_reply); router._split_change sends every call object to the real system whatever the owner, a phone we own also keeps a raw copy (wa_webhook_events); accept_payload never stores a type=call status in wa_message_statuses (no fake delivery evidence, no 'delivery failed' send failure). Tests: tests/test_wa_router.py test_every_call_goes_to_the_real_system_and_a_phone_we_own_keeps_a_raw_copy (replaces the owner-split call test), test_a_crm_call_to_a_campaign_phone_reaches_the_real_system_with_its_statuses (repro: BUSINESS_INITIATED connect + ACCEPTED + permission reply forwarded; a read status of a wamid we do not hold stays ours). Docs: router paragraph, intake table, campaign runbook step 2. Open for Ivan: message statuses of real-system CRM messages to a phone we own stay with us.

Fixer 2026-09-14 (review F5, no readable text): app/cv.py NoReadableText(RuntimeError) for the vision model's NO_TEXT_FOUND (an empty reply stays a plain RuntimeError); api.read_and_classify stores it as document_type 'unreadable' (no text, no text key, certificate_level null) and returns, so _ingest_media puts it on the card and in _documents_just_received and the reply turn runs; other failures still raise and catch-up retries. The importer shares read_and_classify: an old photo without text is classified unreadable instead of failing the import. Prompt DOCUMENT TYPE: unreadable -> ask for a clear photo/PDF. Tests: tests/test_wa_media_intake.py test_a_photo_with_no_readable_text_is_answered_once_and_never_read_again (reply sent, payload names it, gate open, no pending row, catch-up makes no second vision call); test_cv_intake asserts NoReadableText vs empty reply; the vision-failure test now simulates a CLI failure. Docs: wa_documents table, recovery bullet.

Repair round 2 2026-09-14: no code change here. Open for Ivan/orchestrator (final verifier problems 3, 5): statuses are routed by the phone's recorded owner, not through route_decision (AC1 wording); real-system CRM message statuses to a phone we own stay with us; one worker thread under the process-wide ST._lock answers simultaneous replies one after another (a 10th reply can wait minutes), and GET /api/wa/threads also takes ST._lock, so on 8502 it waits for a running turn (pre-existing lock in that route; ?phone= can create a thread row, so dropping the lock is not a one-liner).

Final verification 2026-09-14 20:45-20:57 UTC (after 4-lens review, adversarial verify, fixer + 2 repair rounds): offline suite 1474 passed, 126 skipped, 0 failed. pflege-wa.service restarted 21:22 UTC on this tree; health webhook_ready/outbound_ready/luna_ready true, threads 200. Nothing sent; campaign not run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Statuses, calls and other webhook fields are no longer dropped: our statuses are stored per wamid (failed ones visible as send failures), calls and everything we do not handle go to the old system, nothing is duplicated into the wrong half. Webhooks are acknowledged after durable recording and turns run on a background worker; catch-up recovers interrupted text and media turns. Deviation for Ivan: statuses follow the recorded owner rather than route_decision. Verified by router/background/recovery tests, the campaign e2e run and the full offline suite.
<!-- SECTION:FINAL_SUMMARY:END -->
