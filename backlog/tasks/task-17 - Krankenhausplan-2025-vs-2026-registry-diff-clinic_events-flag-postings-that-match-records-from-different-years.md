---
id: TASK-17
title: >-
  Krankenhausplan 2025 vs 2026: agentic PDF parse, research every difference,
  clinic_events, severity flags on multi-year matches
status: To Do
assignee: []
created_date: '2026-09-09 11:35'
updated_date: '2026-09-09 12:21'
labels:
  - harvester
dependencies: []
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rotkreuzklinik Würzburg closed in April 2026 and was found by accident through a 403. Ivan's point (2026-09-09): the difference between two Krankenhausplan editions is more than one clinic, and each difference needs to be understood before it becomes a record. Only data/registry/krankenhausplan_2026.pdf is on disk; the 2025 edition has to be found and obtained from StMGP. Ivan expects the existing pflege_jobs/sources/krankenhausplan.py parser not to fit the older layout: parse agentically instead -- a swarm of small (Haiku-class) agents that transfer the PDF into a Markdown table page by page, with a second pass checking totals against the PDF's own summary counts. Then the diff. For every differing clinic the sequence is: research first (rename, merger, closure, new site, moved beds, address change -- with the evidence found), then a clinic_events row (clinic_id, at, kind, evidence, resolved_by). Downstream matching: a posting whose candidate clinics span registry years with an event is flagged but stays in matching; it does not disappear. Flags carry a severity assessed once at match time (for example: renamed-only is low, merged is medium, closed is high). The count of flagged postings by severity is visible in the operator dashboard; a jump is a priority signal.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The 2025 edition is parsed and diffed against 2026; every differing clinic has a clinic_events row with a kind and evidence
- [ ] #2 Matching flags a posting whose candidate clinics span registry years with an event and keeps it out of the default match path
- [ ] #3 The count of flagged postings is visible in the operator dashboard
- [ ] #4 The 2025 edition is obtained and transferred to a Markdown table by an agent swarm, with per-page totals reconciled against the PDF's summary
- [ ] #5 Every differing clinic has a researched explanation and a clinic_events row with kind, evidence and resolved_by
- [ ] #6 Matching flags postings whose candidates span registry years with an event, assigns a severity once, and keeps them in the match path
- [ ] #7 Flag counts by severity are visible in the operator dashboard
<!-- AC:END -->
