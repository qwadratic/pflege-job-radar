---
id: TASK-162
title: 'Client name in .claude/agents/copy-lead.md: remove from the public repo'
status: Done
assignee: []
created_date: '2026-09-25 15:38'
updated_date: '2026-10-05 15:40'
labels:
  - security
dependencies: []
priority: medium
ordinal: 162000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Line 20 of .claude/agents/copy-lead.md named a client in the public repo. Rule from career/CLAUDE.md: client names are not named outside. Decision (Ivan 2026-10-05): replace the line with a neutral wording, no history rewrite.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Иван принял решение: убрать имя или оставить
- [x] #2 Если убрать — строка заменена, PR/commit смёржен
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-05 (pflege-clawl). Ivan decided: replace, do not rewrite history. A search over all tracked files found the name only on that line (and in this task, reworded now). Replaced by: The German operator vocabulary of the existing WhatsApp operator panel stays (MANAGER, ANTWORT NOETIG, LUNA AKTIV, LUNA PAUSIERT). Older commits and the earlier file name of this task still contain the name; that stays by decision.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The client name is gone from the working tree of the public repo (one line in copy-lead.md replaced, this task reworded). The repository history keeps it by Ivan's decision.
<!-- SECTION:FINAL_SUMMARY:END -->
