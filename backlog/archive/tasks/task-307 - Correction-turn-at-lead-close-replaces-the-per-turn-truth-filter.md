---
id: TASK-307
title: Correction turn at lead close replaces the per-turn truth filter
status: In Progress
assignee: []
created_date: '2026-09-25 17:56'
updated_date: '2026-09-26 08:48'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 260000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Per-turn grounding rewrites doubled turn latency and still let the model's claims drift. Ivan (2026-09-25): check facts once, when the lead closes (card complete, consent given). Compare what we told the candidate against the posting text, board clinic-registry facts and the IDEOLOGY block; on a false claim re-match, fix our own records, then send a plain update framed as expectation management (no apology, never 'we reported incorrectly'), always with a solution and a closing question. The lead enters the clinic pool in every outcome.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 No per-turn truth rewrite remains; per-turn checks are only the configurable bubble limit, the closing gate and the URL ban
- [ ] #2 build_consent_queues runs the correction turn before queueing and queues the lead in every outcome, send failure included
- [ ] #3 compare() sees posting text, clinic-registry facts and the IDEOLOGY block
- [ ] #4 Correction bubbles carry no apology and end with a question (live probe)
- [ ] #5 IDEOLOGY is one modular block near the top of the brain prompt, reused by compare/compose
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Branch task-305x/correction-turn (5a59304, a5c5cd6), NOT merged. Live close probe OK: 'Kurzes Update: …' / offer / 'Passt das für Sie?', 26.8 s. Opus 5.5 review 2026-09-25: REJECT. Blockers: (1) correction bubbles skip the LINK ban (only the closing gate runs) while the compose payload carries posting links; (2) the close check reads only ledger posting ids, ignores recorded NO INVENTION/COUNT/STALE violations, and an empty ledger short-circuits to nothing_false. Majors: compose gets a bare id for a delisted posting; correction runs outside ST._lock and its save_thread can overwrite a concurrent turn; bubbles are sent before records are saved; api.py builds the live-rail client via T.get_client instead of the injected client (test rail risk). Minors: compare() failure drops code-certain delist findings; bubbles truncated with [:N] instead of rejected; CORRECTION_PICK_KEY unread, WARMING_PICK_KEY stale, no-match path offers no solution. Waiting on Ivan.

2026-09-26: folded into TASK-314 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
