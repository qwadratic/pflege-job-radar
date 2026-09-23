---
id: TASK-282
title: >-
  Remove the autopilot console and its simulated engine, keeping matching.py
  alive
status: To Do
assignee: []
created_date: '2026-09-23 16:23'
labels: []
dependencies: []
priority: medium
project: board
ordinal: 229000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-23: "этот автопайлот полностью у нас... я хочу, чтобы ты убрал". He asked for it as its own request, separate from the console that replaces it (TASK-281).

What autopilot is today, from docs/autopilot.md and the code: an operator console for the whole recruiting funnel -- candidate threads, approvals by risk tier, cohort sends to clinics, clinic-side state machines, e-mail follow-ups, Meta ad campaigns, templates, accounts inventory. It is a proof of concept on SYNTHETIC data with a virtual clock (POST /api/autopilot/tick); nothing in it has ever sent a real WhatsApp message or e-mail. The /autopilot route has answered 503 since 2026-09-08 (app/main.py:autopilot_page) and the chat dock stopped linking to it. So this is removing something that is already dark, not turning off something live.

THE ONE TRAP: app/autopilot/matching.py is NOT dead. app/wa/queue.py imports it as MATCH and calls
rank()/score() against the live app.data snapshot for real candidates on the WhatsApp rail
(app/wa/queue.py:17, :108, :164), and app/cv.py has a related matcher. app/wa/api.py also names it in
a lock comment. Deleting the package wholesale takes the live matching engine with it. It has to move
somewhere honest first -- it was never really autopilot-specific -- and TASK-107 and TASK-207 are
open findings against that same matching code, so whoever moves it should look at where they land.

Also in the blast radius, to be checked rather than assumed: app/settings.py, app/auth.py (the
/api/autopilot gate, which app/wa/api.py:1132 says the WhatsApp lead endpoints copy), app/data.py,
app/main.py, web/autopilot.template.html, data/autopilot.sqlite, docs/autopilot.md and its entry in
docs/index.json, plus tests/test_autopilot.py, tests/test_autopilot_matching.py and the autopilot
cases inside tests/test_auth.py, tests/test_agent_api.py, tests/test_app_api.py, tests/test_paging.py,
tests/test_settings_flags.py and tests/test_wa_queue.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The /autopilot route, its templates and the /api/autopilot/* endpoints are gone, and no route answers on those paths at all rather than answering 503
- [ ] #2 The matching engine that app/wa/queue.py and app/cv.py depend on still works and lives somewhere that is not named after a removed feature
- [ ] #3 The full offline suite runs with no more failures than the baseline it started from
- [ ] #4 docs/autopilot.md and its docs/index.json entry are removed or replaced with a one-line note saying what happened and pointing at the console that replaced it
- [ ] #5 No import of app.autopilot remains anywhere outside the tests that specifically cover the moved matching code
<!-- AC:END -->
