---
id: TASK-312
title: A testing system for dialog quality
status: To Do
assignee: []
created_date: '2026-09-25 17:56'
updated_date: '2026-09-26 08:48'
labels: []
dependencies: []
project: whatsapp
ordinal: 265000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan (2026-09-25): 'тестинг должна быть система'. Today each change is checked with ad-hoc scratch probes (run_probe.py scenarios s2, s3, close). There is no shared scenario set, no recorded verdicts and no regression view across prompt/model changes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Scenario set lives in the repo and replays offline against the brain without touching the rail
- [ ] #2 Each run stores bubbles, timings and a verdict per scenario so runs can be compared
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Existing pieces (2026-09-25):
- tests/test_wa_luna_personas.py: scripted fictional personas through the real CLI, marked llm.
- tests/test_wa_luna_e2e_funnel.py: a model plays the candidate and improvises, through to a queue entry; marked llm.
- app/wa/luna/shadow_run.py: the proposed next reply on a copy of the prod DB, never sends.
- Ad-hoc scratch probes outside the repo.
Gaps: runs are not stored or compared; no timing or cost; no judge against Ivan's rules; llm tests never run by default; nothing shows the chat as the candidate sees it (the notification preview = last bubble). Ivan wants this first; it also carries the TASK-308 bake-off.

2026-09-26: folded into TASK-313 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
