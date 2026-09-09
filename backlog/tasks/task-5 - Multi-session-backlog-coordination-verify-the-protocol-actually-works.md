---
id: TASK-5
title: 'Multi-session backlog coordination: verify the protocol actually works'
status: To Do
assignee: []
created_date: '2026-09-08 22:50'
labels:
  - harness
dependencies: []
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Multiple Claude Code sessions now work this repo concurrently (this session + repo-4a + future ones) and coordinate via the shared backlog/ (backlog.md CLI) instead of stepping on each other -- every session is expected to check the backlog each turn and label its own tasks (e.g. frontend) so work splits cleanly. This needs its own harness/evals track: does a session actually pick up tasks another session filed, avoid duplicate work, and label consistently? Separate work session planned specifically for harness -- this task is the marker for it.
<!-- SECTION:DESCRIPTION:END -->
