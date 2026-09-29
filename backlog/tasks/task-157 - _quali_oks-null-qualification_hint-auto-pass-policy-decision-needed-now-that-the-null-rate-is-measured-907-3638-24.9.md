---
id: TASK-157
title: >-
  _quali_ok's null-qualification_hint auto-pass: policy decision needed now that
  the null rate is measured (907/3638, 24.9%)
status: To Do
assignee: []
created_date: '2026-09-24 23:35'
updated_date: '2026-09-25 00:10'
labels:
  - matching
dependencies: []
ordinal: 157000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-104 (closed 2026-09-24) reduced qualification_hint's null rate from 34.3% to 24.9% by reusing TASK-97's section-extraction, but deliberately did not change app/autopilot/matching.py's _quali_ok(cand_q, job_q) policy: 'if not job_q or job_q == generalistisch: return True' -- a null job_q still silently auto-passes every candidate in the qualification gate for the remaining 907 open postings. This is a scoring-policy decision (should a job with no detectable qualification requirement really auto-pass every candidate, or should it be scored neutrally/excluded/flagged?), explicitly left for Ivan by TASK-104's own closing notes rather than decided unilaterally.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Ivan (or whoever picks this up) decides the intended _quali_ok behavior for a null job_q: keep auto-pass, treat as neutral (no points either way), or something else -- record the decision and reasoning
- [ ] #2 Implement the decided behavior in app/autopilot/matching.py, mutation-tested
- [ ] #3 Live-verify the effect on autopilot candidate scoring for a sample of the 907 affected postings, report before/after
<!-- AC:END -->
