---
id: TASK-139
title: >-
  No verification that clinic-level Fachrichtungen codes are actually correct
  per the Krankenhausplan source
status: To Do
assignee: []
created_date: '2026-09-23 16:26'
updated_date: '2026-09-25 00:10'
labels:
  - db-quality
dependencies: []
priority: low
type: task
ordinal: 139000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-23: is a clinic's own Fachrichtungen set (CHI/INN/PSO/... codes on the clinic record, distinct from per-posting department_hint) actually right? TASK-131/133 check whether a clinic's name/town/operator FIELDS are plausible (not garbage text), but nothing checks whether the fachrichtungen VALUES themselves are accurate against the Krankenhausplan PDF they were extracted from -- a row can have a perfectly well-formed fachrichtungen list that is simply wrong (missing a real department, or listing one the site doesn't have). No existing task covers this; distinct from TASK-136 (parser ROBUSTNESS -- garbled fields) and TASK-133 (load-time plausibility lint) -- this is about VALUE correctness, which those can't detect (a wrong-but-well-formed code list passes both).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Methodology decided: spot-check a sample of clinics' registry fachrichtungen against the source Krankenhausplan PDF entries directly (not against our own postings, which would be circular)
- [ ] #2 A sample (e.g. 20-30 clinics, weighted toward ones already flagged for other reasons -- TASK-131's corrupted rows, TASK-129's attribution issues, this session's beds/live-postings-ratio outliers) checked; error rate reported as a number
- [ ] #3 Go/no-go: is the error rate high enough to warrant a full registry-wide re-check
<!-- AC:END -->
