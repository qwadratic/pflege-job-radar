---
id: TASK-129
title: >-
  One VPS-initiated ssh tunnel unit on our host, and the separate-principal ask
  for the remote machine
status: To Do
assignee: []
created_date: '2026-09-21 01:23'
updated_date: '2026-09-22 06:10'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: medium
type: feature
ordinal: 137000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M4, rescoped by decision-8 (2026-09-21). Two corrections, both from the read-only investigation.

1. THE MACHINE IS NOT A MAC. uname reports Linux macmini-worker1 6.8.0-139-generic, Ubuntu 24.04.5 LTS on Apple hardware. Every launchd, LaunchDaemon, .plist and pmset line in the original task and in plan section 5.8 is void. The primitive is systemd --user plus loginctl enable-linger, already proven in that account without sudo by the existing reverse-tunnel unit (~/.config/systemd/user/macmini-reverse-tunnel.service, Linger=yes, active since 2026-09-21 06:17:16).

2. THE TUNNEL DIRECTION FLIPS. One VPS-INITIATED leg replaces the dual-leg design: ssh -R 127.0.0.1:18502:127.0.0.1:8502 macmini, opened from our side under our own ~/.ssh/id_ed25519. It still satisfies router.py _is_local_caller, because the connection to :8502 is opened by our own ssh client stack, and it retires the UNVERIFIED AllowStreamLocalForwarding dependency (only the readable sshd defaults could be checked; the cloud-init drop-in is unreadable as cursorworker1). Keep -L to a unix socket as an optimisation only, never as the load-bearing path.

So the deliverable is ONE systemd unit on OUR host, which we control, plus a user unit template for the remote side that a human installs.

ExitOnForwardFailure=yes stays load-bearing: without it ssh only warns when a remote bind fails and keeps running, so the supervisor sees a healthy tunnel while nothing is forwarded.

SPLIT OUT: the separate-principal half -- a restricted wa-bridge user with its own authorized_keys line -- needs sudo on the remote machine, which we do not have. It becomes an ask (TASK-141 question 2), not a deliverable. The one restriction we CAN apply unilaterally is on the key coming the other way, and that is TASK-138.

Measured link quality, so nobody plans against an ideal one: RTT 48 ms, 119,092 of 1,041,795 bytes retransmitted (11.4 percent), cwnd pinned at 3, the Ethernet interface DOWN and everything on Wi-Fi. The original plan 72 h soak has zero data: the existing unit was installed on 2026-09-21 with one deliberate restart.

Everything for the remote side is a template. Nothing is installed there, no user is created, no unit is enabled and no service is started by this task.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 deploy/ carries one systemd unit for the VPS-initiated ssh leg on our host, plus a systemd --user unit template and an install runbook for the remote side
- [x] #2 No launchd, LaunchDaemon, .plist or pmset wording survives anywhere in the task deliverables or the runbook
- [ ] #3 The documented ssh invocation includes ExitOnForwardFailure=yes, ServerAliveInterval, ServerAliveCountMax and IdentitiesOnly=yes, and the runbook states why ExitOnForwardFailure is not optional
- [ ] #4 The single -R leg is the load-bearing path and any -L leg is documented as an optimisation, with the reason: it retires the unverified AllowStreamLocalForwarding dependency
- [ ] #5 WA_BRIDGE_TOKEN and WA_BRIDGE_INBOUND_TOKEN are documented as two independent secrets, and META_WHATSAPP_APP_SECRET is documented as never leaving this server
- [ ] #6 The runbook covers the failure drills -- kill the tunnel, reboot the remote machine, break the remote bind -- and states the expected observable outcome of each
- [ ] #7 Tunnel restarts are counted over a soak period and the measured drop rate is recorded, rather than assumed from the plan 72 h figure
- [x] #8 The separate-principal restricted user is recorded as an ask for the remote machine owner, not as a deliverable of this task
- [x] #9 Nothing on the remote machine is created, installed, enabled or started by this task
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: partially built, correctly stays To Do. deploy/wa-bridge/pflege-wa-bridge-tunnel.service exists (installed on the VPS per INSTALL.md section 2, TASK-146) and carries -L only (AC#4 direction matches the design as later refined -- the shipped design settled on -L, not -R, see app/wa/bridge.py/INSTALL.md), ExitOnForwardFailure=yes, ServerAliveInterval=15, ServerAliveCountMax=3 (AC#3, partial: no IdentitiesOnly=yes in the ExecStart line -- grepped, absent). AC#2 met: no launchd/LaunchDaemon/.plist/pmset wording anywhere in deploy/wa-bridge/. AC#5 met in spirit: WA_BRIDGE_TOKEN/WA_BRIDGE_INBOUND_TOKEN and META_WHATSAPP_APP_SECRET are documented as two independent secrets and VPS-only in INSTALL.md section 3 (not re-checked as this AC is closer to TASK-138/docs territory). AC#8 met: the separate-principal restricted user is recorded as an ask rather than a deliverable -- it is TASK-141 question 2, not attempted here. AC#9 met: only the VPS-side unit was installed; nothing found touching the remote machine. NOT MET: AC#1 (no systemd --user unit TEMPLATE for the remote side and no install RUNBOOK for it was found -- INSTALL.md documents the mini running the TASK-143 executor unit, not a remote participant in the tunnel itself); AC#3 (IdentitiesOnly=yes absent from the ExecStart); AC#6 (no failure-drill section -- kill the tunnel / reboot remote / break the remote bind -- found in INSTALL.md); AC#7 (no measured restart count over a soak period was found anywhere; the plan's original 72h-soak-has-zero-data problem this task exists to fix has not been re-measured either). Full offline suite unaffected (deploy/ is not tested by pytest).
<!-- SECTION:NOTES:END -->
