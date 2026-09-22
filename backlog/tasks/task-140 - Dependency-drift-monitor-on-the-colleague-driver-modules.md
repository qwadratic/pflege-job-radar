---
id: TASK-140
title: Dependency-drift monitor on the colleague driver modules
status: To Do
assignee: []
created_date: '2026-09-21 09:11'
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
