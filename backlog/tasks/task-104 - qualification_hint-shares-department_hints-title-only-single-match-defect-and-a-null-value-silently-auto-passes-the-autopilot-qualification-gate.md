---
id: TASK-104
title: >-
  qualification_hint shares department_hint's title-only, single-match defect,
  and a null value silently auto-passes the autopilot qualification gate
status: To Do
assignee: []
created_date: '2026-09-22 16:20'
labels: []
dependencies:
  - TASK-97
references:
  - pflege_jobs/classify.py
  - app/autopilot/matching.py
  - TASK-97
ordinal: 104000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-22 investigation triggered by a request to check department/requirements extraction quality feeding candidate-clinic matching. pflege_jobs/classify.py qualification_hint(title, hauptberuf="") has the exact same two defects TASK-97 already found (and is being fixed) for department_hint: it reads only title plus hauptberuf (an Arbeitsagentur occupation code, not the posting body) through norm_text, and returns next((n for n, r in _QUAL if r.search(s)), None) -- the FIRST matching pattern only, never the description body or the "Ihr Profil"/Anforderungen section that classify.enrich_description() already extracts cleanly into enr_requirements for most live postings.

Measured live 2026-09-22 against v_postings status=open (3166 rows): qualification_hint is null on 1087/3166 (34.3%). app/autopilot/matching.py's _quali_ok(cand_q, job_q) treats a null job_q as an automatic pass ("if not job_q or job_q == generalistisch: return True"), so for those 1087 postings every candidate silently scores the full qualification points in score() regardless of actual fit -- the gate looks like it is checking something but for a third of live postings it is not checking anything at all.

TASK-97 is landing a targeted-section body-extraction approach for department_hint (title plus Aufgaben/Taetigkeiten/Profil-shaped sections, excluding page tail/nav/contact text) plus multi-label support, with false-positive discipline already worked out there. qualification_hint should get the same treatment once that mechanism exists: reuse the section extraction, apply it to the qualification patterns instead of the department patterns. Kept as a separate task rather than folded into TASK-97 because the consumer is different (app/autopilot/matching.py's Matcher.score(), not the web search facet) and TASK-97's acceptance criteria are scoped to the facet/search UI, not to autopilot matching.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 qualification_hint is computed from title plus the same targeted requirements/tasks section(s) TASK-97 extracts for department_hint, reusing that extraction rather than re-deriving a second one
- [ ] #2 The post-fix null rate for qualification_hint is measured and reported as a number against the same live open-postings set, not assumed fixed
- [ ] #3 _quali_ok's null-job_q auto-pass behavior is either resolved by the reduced null rate or explicitly documented as a remaining known gap with its own new count
- [ ] #4 Red-green test against real stored postings: fetch live, confirm the current title-only extraction fails the case, confirm the fix passes; plus a mutation test that reverts the fix and confirms the test goes red
- [ ] #5 A manual sample of at least 20 newly-populated qualification_hint values is checked for correctness; the false rate is reported as a number
<!-- AC:END -->
