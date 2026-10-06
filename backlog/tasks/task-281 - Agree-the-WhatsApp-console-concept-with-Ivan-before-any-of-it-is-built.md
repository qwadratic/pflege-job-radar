---
id: TASK-281
title: Agree the WhatsApp console concept with Ivan before any of it is built
status: To Do
assignee: []
created_date: '2026-09-23 15:54'
updated_date: '2026-09-23 16:22'
labels: []
dependencies: []
priority: high
project: whatsapp
ordinal: 228000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-23: "сначала утверди со мной концепт борды, в которой можно просто смотреть за WhatsApp, не вмешиваясь". He asked for this gate explicitly and by name, ahead of the work itself, because the previous console (autopilot) was built out to eleven feature areas on synthetic data and then switched off without ever being used.

The thing he wants is narrow and he stated its boundary twice: a surface for WATCHING the WhatsApp harness, not for driving it. The single permitted way to act is to schedule your own task into the same phone-ops queue the system already uses (bridge/dispatcher.py). There is no compose box, no send button, no edit of a thread, no state change by hand. Everything else the console shows is a rendering of what is already in the two SQLite stores.

DEPLOYMENT, ALREADY DECIDED BY IVAN (2026-09-23, not open for the concept to revisit):
  * the API lives on THIS server, the VPS tasker-dispatcher-01, next to the existing FastAPI app;
  * it fetches the handset-side data FROM the Mac mini (that is where bridge/ledger.py keeps
    ledger.sqlite, phone_ops, journal, outbound, inbound, broadcast_* and the shots/recordings
    directories -- /home/cursorworker1/.local/share/pflege-wa-bridge/);
  * the frontend is served from the SAME DOMAIN as the board frontend, https://pflege-board.exe.xyz.

The two stores it has to join are on two machines: data/wa.sqlite on the VPS (wa_threads,
wa_messages, wa_documents, wa_ownership, wa_luna_calls) and ledger.sqlite on the mini. The VPS
already reaches the mini over an ssh tunnel for the executor HTTP API and over ssh/scp for media,
so there is a path; what there is not yet is a read API for the ledger.

This task is the conversation and its written outcome, not code. It exists so the family of build
tasks under it cannot start from an assumption nobody checked. What has to come out of it, in
writing: which screens exist and what each one answers; what "schedule my own task" may enqueue and
what it may not; and whether the live screen stream is in the first version or a later one.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A written concept exists in the repo (docs/ or a backlog document) describing every screen and the one write path
- [ ] #2 Ivan has read it and said yes, and his answer is recorded with the date
- [ ] #3 The concept states explicitly which of the build tasks are in the first version and which are deferred
- [ ] #4 The concept names where the console is hosted and how it is reached, given the VPS store and the Mac mini store live on two different machines
- [ ] #5 A written concept exists in the repo (docs/ or a backlog document) describing every screen and the one write path
- [ ] #6 Ivan has read it and said yes, and his answer is recorded with the date
- [ ] #7 The concept states explicitly which of the build tasks are in the first version and which are deferred
- [ ] #8 The concept says how the VPS-side API reads the mini ledger, given that the two SQLite stores sit on different machines
<!-- AC:END -->
