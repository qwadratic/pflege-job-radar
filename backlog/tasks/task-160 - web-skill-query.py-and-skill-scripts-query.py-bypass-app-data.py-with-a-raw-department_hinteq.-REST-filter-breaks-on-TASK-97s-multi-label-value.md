---
id: TASK-160
title: >-
  web/skill/query.py and skill/scripts/query.py bypass app/data.py with a raw
  department_hint=eq. REST filter, breaks on TASK-97's multi-label value
status: To Do
assignee: []
created_date: '2026-09-25 00:17'
updated_date: '2026-09-25 00:18'
labels:
  - db-quality
dependencies: []
ordinal: 160000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by TASK-97's own agent (2026-09-24), flagged but not filed as its own task. web/skill/query.py and skill/scripts/query.py query v_postings directly via PostgREST with department_hint=eq.<value>, bypassing app/data.py's filter_jobs() entirely. Since TASK-97, department_hint is a '|'-joined multi-label string on the wire (e.g. 'Intensiv/IMC|Anästhesie'); a raw eq. filter only matches a posting whose department_hint is EXACTLY that one value, so a genuinely multi-department posting (now much more common post-TASK-97, 1912 of 3638 open postings carry a label) will not match either of its own departments via this path. app/data.py's own /api/jobs facet+filter already handles this correctly (splits on '|', intersection match) -- only these two skill-facing query scripts bypass it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Confirm exactly which query shapes in web/skill/query.py and skill/scripts/query.py filter on department_hint (or any other now-multi-valued field) via a raw PostgREST eq.
- [ ] #2 Fix to match on any one label (e.g. ilike/contains against the '|'-joined string, or split client-side and OR the candidates) -- same semantics app/data.py's filter_jobs already implements
- [ ] #3 Live-verified: a known multi-department posting is findable via the skill's own query path by either one of its departments
<!-- AC:END -->
