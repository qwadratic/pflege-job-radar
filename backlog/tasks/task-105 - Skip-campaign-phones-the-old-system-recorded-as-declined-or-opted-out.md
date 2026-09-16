---
id: TASK-105
title: Skip campaign phones the old system recorded as declined or opted out
status: To Do
assignee: []
created_date: '2026-09-14 22:15'
labels: []
dependencies:
  - TASK-102
  - TASK-103
type: feature
ordinal: 105000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-14: opt-outs and declines recorded by the old system must be honoured, not only a typed Stopp found in the imported chat history. The old system records them beyond chat text, e.g. candidate lifecycle closed_reason declined_opt_out (apps/connectors/candidate_lifecycle.py, candidate_goal_orchestrator.py) and outreach status declined (job_wohnung_outreach). Today import_history.py only derives stop_messages from chat bodies, and the campaign sender only sees them when --import-history-* is given.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 the history import reads the old system opt-out/decline records read-only (operator SQL documented in deploy/import-history.example.sql, written from old-system code) and reports per phone which record and when
- [ ] #2 the campaign dry-run and --send never send to a phone with such a record or a Stopp in its old history, reporting it as skipped with the reason; the check runs for every send (an explicit, logged override flag is the only way to send without the old-system source)
- [ ] #3 an imported decline marks the thread declined (TASK-101 semantics: no follow-ups, silence) so Luna does not treat a later message as a fresh lead without re-engagement
- [ ] #4 offline tests with the synthetic old-schema fixture cover lifecycle opt-out, outreach decline, chat Stopp, and the override flag; docs and runbook updated
<!-- AC:END -->
