---
id: TASK-66
title: >-
  Candidate queue and clinic-matching endpoint for consenting WhatsApp
  candidates
status: Done
assignee:
  - '@claude'
created_date: '2026-09-12 16:00'
updated_date: '2026-09-12 17:09'
labels: []
dependencies:
  - TASK-64
ordinal: 66000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan asked that once a candidate consents to having their anonymized profile shared, the harness produce a real queue of (candidate, matching clinic subset) and, where known, a clinic contact -- with a data model that converges to (list of candidates, subset of clinics) and an endpoint a human can read from, explicitly not an automatic email sender. app/autopilot/matching.py already has a real, reusable scoring/ranking engine (score/rank/filter_candidates/cohort_preview) meant for exactly this shape, but app/autopilot own SQLite is explicitly synthetic-PoC-only with its UI disabled since 2026-09-08 -- real candidate data must not be written into it. See /home/claude/.claude/plans/wise-enchanting-crayon.md section 4 for the trigger-placement and idempotency reasoning (the queue build must happen in app/wa/api.py after the thread save succeeds and outside the per-message lock, not inside luna_brain.py:turn()).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New app/wa/queue.py:card_to_candidate maps the Luna card plus CV profile into the candidate shape app.autopilot.matching.rank expects, deriving Regierungsbezirk from the card city via the D.snapshot() clinics lookup (the card own region field holds an unrelated out-of-scope-Bundesland gate value and must not be reused for this)
- [x] #2 build_queue_entry calls app.autopilot.matching.rank directly against D.snapshot(), resolves each ranked clinic contact via TASK-64 clinic_contacts table, and upserts into new wa_queue_candidates and wa_queue_matches tables (with a unique constraint on phone/clinic_id/posting_id) in the same sqlite file as app/wa/store.py -- not into app/autopilot/db.py
- [x] #3 The trigger lives in app/wa/api.py: after a turn where anonymous_send_consent newly became true, and only after ST.save_thread succeeds, and outside the per-message threading.RLock() critical section
- [x] #4 New GET /api/wa/queue and GET /api/wa/queue/mailing-list endpoints exist; mailing-list is a preview/report shape only and sends nothing
- [x] #5 Unit tests cover card_to_candidate and build_queue_entry with a fixture ranked-clinics list, and both new endpoints
- [x] #6 Full offline suite stays green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New app/wa/queue.py: card_to_candidate(card, cv_profile) maps Luna card + CV profile fields into the candidate dict app.autopilot.matching.rank() expects (role_class, city, region=derived Regierungsbezirk via D.snapshot() city lookup -- NOT card['region'] which holds an unrelated out-of-scope-Bundesland gate value, qualification, departments, german_level, anerkennung_status).
2. build_queue_entry(phone, card, cv_profile): calls app.autopilot.matching.rank() directly against D.snapshot()['clinics']/['jobs'], resolves each ranked clinic's contact via app.wa.luna.contacts.get_contact (TASK-64), upserts into two new tables in the wa sqlite file (same C.SQLITE_PATH, own schema block like contacts.py's pattern): wa_queue_candidates(phone primary key, consented_at, profile_json, status) and wa_queue_matches(id, phone, clinic_id, posting_id, score, reasons_json, contact_email, contact_source, unique(phone,clinic_id,posting_id)).
3. Hook in app/wa/api.py, NOT luna_brain.py: after LB.turn() runs and the thread is saved via ST.save_thread(c,t), and outside the per-message lock, check if anonymous_send_consent newly became True (compare pre/post card) and call build_queue_entry() then.
4. New app/wa/queue_api.py: GET /api/wa/queue (candidates x matched clinics with contact email/source) and GET /api/wa/queue/mailing-list (flattened candidate x clinic x email preview -- sends nothing). Mount alongside app/wa/api.py's router.
5. Unit tests for card_to_candidate/build_queue_entry with a fixture ranked-clinics list; endpoint tests for both routes; a test for the api.py trigger placement (post-save, outside lock, idempotent upsert on repeat consent).
6. Run offline suite, update docs/whatsapp.md, backlog notes/finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built app/wa/queue.py (card_to_candidate, build_queue_entry, queue_rows, mailing_list_rows) and app/wa/queue_api.py (GET /api/wa/queue, GET /api/wa/queue/mailing-list, mounted in app/main.py). Reuses app.autopilot.matching.rank() directly against D.snapshot(); own wa_queue_candidates/wa_queue_matches tables (upsert, unique(phone,clinic_id,posting_id)) in the same sqlite file as store.py and contacts.py, not autopilot's own DB. Trigger lives in app/wa/api.py:_handle_one (compares anonymous_send_consent before/after the LB.turn() call) and handle_payload (collects newly-consented phones during the locked loop, calls build_queue_entry after the with-block exits, i.e. after ST.save_thread succeeded and outside ST._lock).

Both new endpoints added to auth.py:OWNER_READ_PREFIXES (same PII class as /api/wa/threads) and to test_auth.py's DENIED table for real auth-gating coverage, rather than hand-rolling a login flow in this task's own test file.

Bug found and fixed via testing, not assumed: build_queue_entry's contact lookup (CT.get_contact) failed with 'no such table: clinic_contacts' when queue.db()'s connection had never had contacts.py's own SCHEMA applied on it -- fixed by having queue.db() apply both schemas on the same connection (same bug class TASK-62 already hit once with the MCP tool's own get_clinic_contact).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
New app/wa/queue.py + app/wa/queue_api.py give consenting WhatsApp candidates a real (candidates x matched clinics) queue: card_to_candidate() derives a proper Regierungsbezirk from the card's city (never the card's own unrelated out-of-scope-Bundesland region field), build_queue_entry() reuses app.autopilot.matching.rank() against the live snapshot and resolves TASK-64 clinic contacts, upserting into two new purpose-built tables -- explicitly not into app.autopilot's synthetic-PoC-only database. The trigger sits in app/wa/api.py, firing only after a turn's consent flip is durably saved and after the per-message lock is released, so a match build never blocks other inbound threads. GET /api/wa/queue and GET /api/wa/queue/mailing-list are owner-gated read-only previews that send nothing. Verified: 21 new offline tests (queue logic, both endpoints via direct handler calls, and the api.py trigger's fresh/repeat/no-consent/wrong-brain cases via a faked luna_brain.turn) plus 2 new auth-gating cases in test_auth.py's existing DENIED table. Full offline suite: 961 passed, same 6 pre-existing unrelated failures.
<!-- SECTION:FINAL_SUMMARY:END -->
