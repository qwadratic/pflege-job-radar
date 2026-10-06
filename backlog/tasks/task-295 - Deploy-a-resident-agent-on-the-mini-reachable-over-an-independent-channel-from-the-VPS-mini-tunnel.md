---
id: TASK-295
title: >-
  Deploy a resident agent on the mini, reachable over an independent channel
  from the VPS-mini tunnel
status: To Do
assignee: []
created_date: '2026-09-24 09:04'
updated_date: '2026-09-24 09:08'
labels:
  - whatsapp
  - operational
  - reliability
  - infrastructure
dependencies: []
ordinal: 248000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-24, live incident: the reverse SSH tunnel between this VPS and the phone-rail mini dropped twice in one session (36 min, then 30+ min) with no way to intervene from the VPS side -- reaching the mini to diagnose or fix anything requires the very channel that is down, a chicken-and-egg problem. Ivan's fix, corrected from an earlier draft of this task that proposed Tailscale/a mesh VPN: no separate networking layer needed -- Claude Code itself is the independent channel. Run Claude Code directly on the mini (same product this VPS's own sessions already run on); it becomes reachable through Claude Code's own remote-session/harness connectivity (Remote Control / cloud sessions, the same mechanism ListAgents already surfaces for 'other Claude sessions on this machine' and 'your account's other sessions'), which does not route through pflege-wa-bridge-tunnel.service's SSH forward at all. During an outage, Ivan (or another session) can reach that mini-resident Claude Code session directly and have it check/restart the reverse-tunnel client, inspect the mini's own network state, etc. -- without needing the SSH tunnel to already be up. 'И это будет вообще идеально' -- Ivan's own framing of the end state. Filed per his instruction to just file it for now, not build it yet.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Claude Code is installed and authenticated on the mini, running as a persistent session/service (not a one-off terminal invocation that dies on logout or reboot)
- [ ] #2 That mini-resident session is reachable via Claude Code's own Remote Control/cloud-session mechanism from elsewhere (this VPS's own sessions, or Ivan's client directly) -- verified while the pflege-wa-bridge-tunnel SSH forward is deliberately stopped, so it is proven independent, not just coincidentally both up
- [ ] #3 An operator can send it an instruction (e.g. 'check/restart the reverse tunnel client, report its status') and observe the result through that same Remote Control channel
- [ ] #4 The setup and its own failure mode are documented -- what happens if the mini loses network entirely (both channels down), so this does not quietly become a second unmonitored single point of failure
<!-- AC:END -->
