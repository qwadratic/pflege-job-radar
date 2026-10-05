---
id: TASK-345.12
title: >-
  Daria desk: Daria, the AI employee on daria.s@pflege-connect.work, answers
  Ivan and Valentyn at any time, recommends one job each morning, works her own
  task pipeline and plans mailings
status: To Do
assignee:
  - '@claude'
created_date: '2026-10-01 18:09'
labels:
  - email
dependencies: []
parent_task_id: TASK-345
priority: high
ordinal: 278000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-01, while launching the second nurse-79 wave: "если попросит более трудную корректировку или любые вопросы не требующие кода - используй все свои доступы чтобы отвечать"; "вцелом отвечать на вопросы дарья должна всегда быть способна"; "и ежедневно утром - одно полезное дело порекомендовать, до вечера сделать если дали отмашку + взять безболезненную пачку задач из пайплайна"; "должна также уметь планировать рассылки, которые можно стопнуть или исключить клиники отмашкой". Her toolset, also Ivan 2026-10-01: sales_brain (read), the same board tools the WA harness has, and her own section in the backlog as a task pipeline (backlog project "daria"); no server shell, because a forged operator address must not reach the server. Today only a running scheduled batch reads operator mail, it answers anything beyond stop/skip/status with "operator needed", and two batches running on the same sender box (wave 1 until 09.10, wave 2 from 02.10) would both answer, and both act on, every operator mail.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every operator mail to the sender box gets exactly one answer, from Daria, whether or not a batch is running
- [ ] #2 The subtasks are done
<!-- AC:END -->
