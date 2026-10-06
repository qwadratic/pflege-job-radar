---
id: TASK-305
title: Yes/no answer recovery checked against real candidate replies
status: To Do
assignee: []
created_date: '2026-09-25 07:59'
updated_date: '2026-09-26 08:48'
labels:
  - wa-transport
dependencies:
  - TASK-382
project: whatsapp
ordinal: 258000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Successor to TASK-224's AC#7 (superseded, closed 2026-09-25): numbered option lists are gone (_OBJECTIVE_ORDER in app/wa/luna_brain.py bans them; the job search takes several cities/criteria in one call, so answers no longer need narrowing). What remains live is the two-button yes/no keyword tier in app/wa/luna/choices.py, already covered by unit tests against synthetic input. This task is the same corpus-check idea TASK-224's M7 described, narrowed to what still exists: once real candidates have answered two-option (yes/no) questions on the phone rail, run the aggregate-count check from TASK-224's M7 against that message history -- counts only, no candidate content copied into the repo, the task or any report. Depends on TASK-382 (UAT broadcast), the first source of real candidate replies.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Aggregate-count check run against real candidate replies to two-button (yes/no) offers on wa_messages history, no candidate content copied anywhere
- [ ] #2 Report states how often the keyword tier matched, mismatched and fell through to Luna as free text
- [ ] #3 If the match rate is below what TASK-224's own 90pct-or-escalate rule required, the task stops and escalates rather than adding regex; the keyword tier is adjusted only on a real, observed failure mode
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-26: folded into TASK-313 (Ivan's backlog consolidation: fewer tasks, grouped by priority and risk area). Its acceptance criteria and context were carried over. This file keeps the full original text.
<!-- SECTION:NOTES:END -->
