---
id: TASK-27
title: >-
  Adapter completeness: mutation tests prove each check goes red for the right
  adapter
status: To Do
assignee: []
created_date: '2026-09-10 07:49'
labels:
  - harvester
dependencies: []
ordinal: 27000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A completeness test that never goes red is worthless. For each adapter that passes its checks, a mutation test monkeypatches one breakage from adapter_contract.MUTATIONS (stop after the first page, drop the description, store the API self-link, skip detail fetches) and asserts that exactly the matching check fails and its message names that adapter and that board. Marked mutation.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 For every passing adapter, each of the four mutations makes exactly the corresponding check fail
- [ ] #2 The failure message contains the adapter name, the board url and the check name
- [ ] #3 No mutation leaks: after each mutation test the adapter behaves normally again
<!-- AC:END -->
