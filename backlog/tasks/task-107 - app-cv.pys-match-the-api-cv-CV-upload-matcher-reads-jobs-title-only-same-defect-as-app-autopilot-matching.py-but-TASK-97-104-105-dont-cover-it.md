---
id: TASK-107
title: >-
  app/cv.py's match() (the /api/cv CV-upload matcher) reads jobs title-only,
  same defect as app/autopilot/matching.py, but TASK-97/104/105 don't cover it
status: To Do
assignee: []
created_date: '2026-09-22 16:27'
labels: []
dependencies:
  - TASK-97
  - TASK-105
references:
  - app/cv.py
  - app/autopilot/matching.py
  - app/main.py
  - TASK-97
  - TASK-104
  - TASK-105
  - TASK-106
ordinal: 107000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-22 follow-up to the same department/requirements-extraction-quality investigation that produced TASK-97/104/105. Those three all scope their fix to app/autopilot/matching.py's score()/rank() (the outreach/autopilot engine, docs/autopilot.md feature 5) and/or the web search facet. There is a second, separate candidate<->job matcher in this codebase that none of them mention: app/cv.py's analyse()/match(), reachable live via POST /api/cv (app/main.py:302) -- upload a CV, get ranked matching postings back.

match()'s department block checks j.get('department_hint') first (it will inherit whatever multi-label body-extraction TASK-97 lands), but its fallback path scans the candidate's skill tags only against title_low = (j.get('title') or '') + ' ' + (j.get('department_raw') or '') -- never against description, and never against enr_requirements/enr_language_req/enr_experience (the same already-extracted-but-unrouted fields TASK-105 found missing from app/autopilot/matching.py). match() does not read qualification_hint at all, so nothing in this matcher today cross-checks a candidate's stated qualification against a posting's.

The asymmetry is sharper here than in the autopilot matcher: the CANDIDATE side of this exact match() call is already read richly by profile_from_text() -- a 21-tag department/skill regex list applied to the full CV text (not a title-equivalent field), plus an optional, already-wired, already-working LLM refine call (_llm_refine(), confirmed reachable through the live endpoint) -- while the JOB side of the same call is read title-only. Candidate-side richness paired with job-side poverty, inside one matcher.

Same reasoning TASK-104 used to justify staying separate from TASK-97 ('the consumer is different') applies here: a third consumer of the same underlying signal-quality problem, untouched by any task filed today.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Once TASK-97 lands, match()'s primary department check (j.get('department_hint') against candidate departments) is confirmed to read the new multi-label value rather than assuming it without checking
- [ ] #2 match()'s title+department_raw-only skills fallback is measured against the full description text on live data: how many jobs' department/skill score contribution would change if description were included, reported as a number
- [ ] #3 Decision recorded on whether match() should read enr_requirements/enr_language_req/enr_experience once TASK-105 exposes them through JOB_COLS, and whether it should check qualification_hint at all (it currently does not)
- [ ] #4 Decision recorded on whether the LLM-extraction option (TASK-106) should extend to posting-side data consumed here, or whether the existing candidate-side _llm_refine() call is sufficient on its own
<!-- AC:END -->
