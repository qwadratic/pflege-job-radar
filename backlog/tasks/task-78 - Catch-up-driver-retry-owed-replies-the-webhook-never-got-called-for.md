---
id: TASK-78
title: 'Catch-up driver: retry owed replies the webhook never got called for'
status: In Progress
assignee: []
created_date: '2026-09-13 11:14'
updated_date: '2026-09-13 11:26'
labels: []
dependencies: []
ordinal: 78000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Recon found our harness has zero resilience if a webhook call never arrives or fails before generating a reply -- purely synchronous, no poller, unlike the real system's 3-minute timer (their own --help text: webhook wake may fail, catch-up is the resilient primary path). Promote shadow_run.py's read-only query logic (phones_owed_a_reply, ball_for) into a real driver that can actually call the brain and send -- gated by the same reply-turn claim and rate-limit machinery as the webhook path, so it never double-answers or bypasses the cap. Manually/cron-invoked in this harness (no systemd timer is being installed as part of this task -- that is an operational decision for whoever runs this in a real environment).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New CLI (e.g. python -m app.wa.luna.catchup) finds every thread with ball_for()=='us' and attempts a real reply for each, via the same shared reply-and-send path api.py's webhook handler uses
- [x] #2 Goes through the same claim_reply_turn/finish_reply_turn_claim and rate-limit gates as the webhook path -- no separate/duplicated decision logic
- [x] #3 Actually calls Meta send when WA_AUTOSEND is on, unlike shadow_run.py which never sends -- this is deliberately the live counterpart, not another dry-run tool
- [x] #4 A thread already claimed/in-progress or already answered is skipped, not double-processed
- [x] #5 Unit tests cover: an owed thread gets a real reply, a thread already claimed by a concurrent 'webhook' is skipped, a rate-capped thread is skipped and remains owed for the next run
- [x] #6 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/luna/catchup.py: run()/main() (python -m app.wa.luna.catchup [--phones]). Reuses shadow_run.phones_owed_a_reply() for the owed-thread query (one definition, not two) and app.wa.api.process_owed_turn() for the actual decide-and-send (same claim/rate-limit/failure-recording as the webhook path -- no duplicated logic). Unlike shadow_run.py this runs against the real database and really sends when WA_AUTOSEND is on. Known, named scope limit: only retries the brain-decided reply path, not a stuck flat media-ack send (a narrower, separate gap). 7 new tests. No systemd timer installed -- deploy cadence is an operational decision, out of scope here.
<!-- SECTION:NOTES:END -->
