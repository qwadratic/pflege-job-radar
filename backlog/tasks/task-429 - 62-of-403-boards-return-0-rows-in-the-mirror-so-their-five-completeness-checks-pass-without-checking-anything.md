---
id: TASK-429
title: >-
  62 of 403 boards return 0 rows in the mirror, so their five completeness
  checks pass without checking anything
status: To Do
assignee: []
created_date: '2026-10-06 06:16'
labels:
  - crawler-coverage
  - adapter-testing
dependencies: []
priority: medium
ordinal: 299000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-197 mirror re-record on 2026-10-05: 62 of the 403 recorded boards return 0 rows from their adapter, which makes the five checks of tests/test_adapter_completeness.py vacuous for them (nothing to compare). For each of the 62: decide from the recorded pages whether the board is genuinely empty (the page shows no vacancy) or the adapter misses its vacancies; a genuine empty board gets a named, visible reason, an adapter miss becomes a red test on the mirror and a fix. Related: TASK-191 (114 boards degraded every night) and TASK-190.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All 62 boards are listed with a verdict (genuinely empty or adapter miss) and the recorded page that shows it
- [ ] #2 Every adapter miss has a red test on the mirror and a fix; every genuinely empty board carries a named reason in tools/mirror.py status, not a silent pass
<!-- AC:END -->
