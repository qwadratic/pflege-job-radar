---
id: TASK-66
title: >-
  Candidate queue and clinic-matching endpoint for consenting WhatsApp
  candidates
status: To Do
assignee: []
created_date: '2026-09-12 16:00'
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
- [ ] #1 New app/wa/queue.py:card_to_candidate maps the Luna card plus CV profile into the candidate shape app.autopilot.matching.rank expects, deriving Regierungsbezirk from the card city via the D.snapshot() clinics lookup (the card own region field holds an unrelated out-of-scope-Bundesland gate value and must not be reused for this)
- [ ] #2 build_queue_entry calls app.autopilot.matching.rank directly against D.snapshot(), resolves each ranked clinic contact via TASK-64 clinic_contacts table, and upserts into new wa_queue_candidates and wa_queue_matches tables (with a unique constraint on phone/clinic_id/posting_id) in the same sqlite file as app/wa/store.py -- not into app/autopilot/db.py
- [ ] #3 The trigger lives in app/wa/api.py: after a turn where anonymous_send_consent newly became true, and only after ST.save_thread succeeds, and outside the per-message threading.RLock() critical section
- [ ] #4 New GET /api/wa/queue and GET /api/wa/queue/mailing-list endpoints exist; mailing-list is a preview/report shape only and sends nothing
- [ ] #5 Unit tests cover card_to_candidate and build_queue_entry with a fixture ranked-clinics list, and both new endpoints
- [ ] #6 Full offline suite stays green
<!-- AC:END -->
