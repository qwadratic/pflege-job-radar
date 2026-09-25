---
id: TASK-162
title: >-
  Клиентское имя "NDT Operator" в .claude/agents/copy-lead.md — убрать из
  публичного репо
status: To Do
assignee: []
created_date: '2026-09-25 15:38'
labels:
  - security
  - decision-needed
dependencies: []
priority: medium
ordinal: 162000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Строка ~20 .claude/agents/copy-lead.md: "German operator vocabulary from NDT Operator stays (...)" называет клиента в публичном репозитории. По правилу career/CLAUDE.md клиентские имена (NDT Group/NDT Operator) не называются наружу. Репо pflege-job-radar публичный (PUBLIC на GitHub). Нужно решение Ивана: заменить на нейтральное обозначение (например "the clinic operator") или оставить как есть — не делать без его да.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Иван принял решение: убрать имя или оставить
- [ ] #2 Если убрать — строка заменена, PR/commit смёржен
<!-- AC:END -->
