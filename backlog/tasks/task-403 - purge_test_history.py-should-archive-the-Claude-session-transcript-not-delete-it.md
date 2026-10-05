---
id: TASK-403
title: >-
  purge_test_history.py should archive the Claude session transcript, not delete
  it
status: To Do
assignee: []
created_date: '2026-09-23 02:09'
updated_date: '2026-10-05 13:36'
labels: []
dependencies: []
project: whatsapp
ordinal: 171000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The daily purge (pflege-wa-purge-test.timer, 03:00 Europe/Berlin, --older-than-hours 0 --apply by default) and manual purge runs both delete a test thread's Claude Code session transcript (C.LUNA_SESSION_STORE) outright when wiping the thread. Clearing it from the card so a fresh test doesn't resume from stale context is correct; destroying the file itself is not — it throws away a real conversation that is sometimes worth reviewing later. Ivan hit this directly (2026-09-23): asked to revisit an old exchange from a thread whose history had been purged earlier the same session at his own request, and the transcript was gone, unrecoverable.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 purge_test_history.py moves the session transcript file to an archive location (not the working session directory) instead of deleting it, keyed so it can still be found by phone + timestamp
- [ ] #2 the card's _session_id is still cleared/reset the same way as today, so a fresh test never resumes from the archived transcript
- [ ] #3 the daily timer's default run (--older-than-hours 0 --apply) uses the same archive behavior, not a separate code path
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-05: renumbered from TASK-163 by backlog doctor --fix (two tasks had the ID TASK-163). A mention of TASK-163 in a task text written before this date may mean this task, not the one that kept TASK-163.
<!-- SECTION:NOTES:END -->
