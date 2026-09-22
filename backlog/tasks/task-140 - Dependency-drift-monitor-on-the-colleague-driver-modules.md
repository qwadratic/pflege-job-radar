---
id: TASK-140
title: Dependency-drift monitor on the colleague driver modules
status: Done
assignee: []
created_date: '2026-09-21 09:11'
updated_date: '2026-09-22 06:08'
labels:
  - wa-transport
dependencies:
  - TASK-134
references:
  - /home/claude/plans/2026-09-21-macmini-revision.md
priority: medium
type: feature
ordinal: 148000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
We are about to import three files we do not own, from a tree that is designed to be thrown away.

~/wa-phone-outreach is a disposable agent worktree: cat .git there reads gitdir: .../clinic-dispatcher/.git/worktrees/wa-phone-outreach, on branch cursor/wa-phone-outreach-7972. A git worktree remove, a branch checkout, or the next Cursor Agent run swaps device.py, whatsapp.py and inbox.py under us with no signal. The package is two days old and was written by an agent in two commits; it will keep moving.

Silent drift on a driver library is the worst failure shape available here, because it does not raise -- it changes behaviour. A renamed status string or a moved selector turns a verified tick into an unverified one, and unverified must never become sent (decision-8 item 5).

Record and serve in health: git -C ~/wa-phone-outreach rev-parse HEAD and git worktree list, plus a content hash of each of the three modules we import. Alarm on any change to those three, and on the worktree disappearing.

Read-only: this task reads his tree over ssh and writes nothing there.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Health records and serves the head commit of his tree and the output of git worktree list
- [ ] #2 A content hash of device.py, whatsapp.py and inbox.py is recorded, and a change to any of the three raises an alarm naming which file changed
- [ ] #3 The worktree disappearing or the branch changing raises an alarm distinct from a file-content change
- [ ] #4 The alarm carries the pinned versions we validated against, so the operator can see what drifted from what
- [ ] #5 A test drives each alarm condition against a fake tree state with no ssh and no network
- [ ] #6 Nothing is written to the remote machine; all reads are read-only over ssh
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
CLOSED AS SUPERSEDED, 2026-09-22, not done as written. This task exists to monitor drift on three files we import from the colleague's disposable worktree (device.py, whatsapp.py, inbox.py). TASK-142 (created after this task) replaced that import with an independent implementation, bridge/adb_driver.py, that imports nothing from their tree: 'the technique below was read off that tree and reimplemented; nothing is imported from it, and this file keeps working if that directory disappears tonight' (bridge/adb_driver.py docstring). There is therefore no live import to drift-monitor: the constants that WERE adopted (pinned serial, forbidden serial, lock path, ADBKeyboard IME, typing speed range) were copied once as a deliberate one-time adoption, not imported live, and a change to their tree cannot silently change our behaviour any more. bridge/driver.py:175 keeps a one-line docstring reference ('dict identifying the driver code actually loaded (TASK-140 drift monitor)') but no git-rev-parse/worktree-list/content-hash alarm system was built, and none is needed for the reason this task gives. TASK-132 (bridge health timer) still depends on this task in the backlog graph; that dependency should be dropped when TASK-132 is next worked, since TASK-132's remaining signals (tunnel restarts, flock contention, free disk) do not need it. No acceptance criterion is checked: the drift this task guards against can no longer happen.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Obsolete, not implemented. Superseded by TASK-142: the executor no longer imports the colleague's device.py/whatsapp.py/inbox.py at all (bridge/adb_driver.py is an independent reimplementation), so there is no live driver-library import left to drift-monitor. The constants worth keeping (serials, lock path, IME, typing speed) were adopted once, not imported, and are pinned in bridge/adb_driver.py's own docstring instead of a runtime alarm.
<!-- SECTION:FINAL_SUMMARY:END -->
