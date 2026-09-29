---
id: TASK-138
title: >-
  Job-card badges hard-render one department_hint value -- can't show a posting
  spanning multiple departments even once TASK-97 lands multi-label extraction
status: To Do
assignee: []
created_date: '2026-09-23 16:26'
updated_date: '2026-09-25 00:10'
labels:
  - frontend
dependencies: []
priority: low
type: task
ordinal: 138000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-23: are job-card badges correct, including when a posting covers multiple departments? Checked live: web/index.template.html:364 renders exactly one department badge per posting -- el('span',{class:'tag',text:j.department_hint}) -- and department_hint itself (pflege_jobs/classify.py) is a SCALAR field: classify.department_hint returns next((n for n,r in _DEPT if r.search(s)), None), the FIRST regex match only (same defect TASK-97 already documents: 'Вакансия на интенсивную терапию с анестезией получает одну метку из двух'). So today a multi-department posting both loses the second label at the classification layer (TASK-97's own finding) AND has nowhere to render it even if it didn't -- the frontend has no multi-badge loop, just one optional tag.\n\nDepends on TASK-97 landing multi-label department extraction first (its own AC already scopes multi-label support) -- this task is the frontend half: render every department_hint value once the field becomes a list, not just the first.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Confirm TASK-97's department_hint output shape once implemented (list vs scalar) before touching the frontend
- [ ] #2 web/index.template.html's job row (and the job detail view, j_dept at line ~976) renders one badge/value per department_hint entry, not just the first
- [ ] #3 Verified against a real live posting known to span 2+ departments (TASK-97's own example: Intensivpflege + Anästhesie)
<!-- AC:END -->
