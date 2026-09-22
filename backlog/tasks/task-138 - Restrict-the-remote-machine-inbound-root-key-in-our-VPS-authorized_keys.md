---
id: TASK-138
title: Restrict the remote machine inbound root key in our VPS authorized_keys
status: To Do
assignee: []
created_date: '2026-09-21 09:10'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-21-macmini-revision.md
priority: high
type: chore
ordinal: 146000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Unilateral, one line on our side, and the largest single exposure on either machine. Everything else in every option considered was etiquette; this is the only real enforcement available to us.

The remote machine holds an unrestricted root key into this server: ~/.ssh/config there has Host hetzner / User root / IdentityFile ~/.ssh/hetzner_root, and the matching line in our /root/.ssh/authorized_keys (key comment macmini-worker1, SHA256:/7MMG4ci..., added 2026-09-17 11:04:46) carries no from=, no command= and no restrict. sshd LogLevel is unset, so nobody can know what those sessions ran. Both the original plan (lines 129 and 340) and decision-6 forbade reusing exactly that key for our own tunnel; neither addressed the key that is already there.

It is in active use: farm_autopilot.py:84,:209,:568 shells over it, and :568 pipes Python into ssh hetzner "... python3 -". So restricting it will break something unless we ask first what it needs. That is why this task has a heads-up step, not because the restriction is negotiable.

Scope is our own authorized_keys file only. Do not touch his key material, his config, or anything on his machine.

CAUTION: this edits ssh access to the production VPS. It is trivially self-lockout-shaped. Keep a second authenticated session open while editing, and verify from a third before closing either.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The colleague is asked, before any change, exactly what farm_autopilot.py needs over that key, and the answer is recorded
- [ ] #2 The authorized_keys line for macmini-worker1 carries restrict plus the narrowest from=, command= and forwarding options that still satisfy the recorded answer
- [ ] #3 The restriction is verified from the remote side: the permitted invocation still works and an arbitrary shell command is refused
- [ ] #4 Root ssh access for a human operator is proven still working from an independent session before the editing session is closed
- [ ] #5 Only our own /root/.ssh/authorized_keys is modified; no file, key or config on the remote machine is touched
- [ ] #6 sshd LogLevel is raised enough to record executed commands for that key, or the decision not to is written down with its reason
<!-- AC:END -->
