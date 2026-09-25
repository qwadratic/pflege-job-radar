---
id: TASK-294
title: >-
  Monitor the reverse SSH tunnel to the phone-rail mini and alert on a prolonged
  outage
status: Done
assignee: []
created_date: '2026-09-24 08:28'
updated_date: '2026-09-24 08:30'
labels:
  - whatsapp
  - operational
  - reliability
dependencies: []
ordinal: 247000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live incident, 2026-09-24 01:07-01:44 UTC: the mini's reverse SSH tunnel (127.0.0.1:2222 on tasker-dispatcher-01, ssh config Host macmini -- both pflege-wa-bridge-tunnel.service and any manual ssh/scp to the mini go through this one channel) dropped for 36 minutes with zero alert anywhere; only noticed via an ad-hoc manual check. pflege-wa-bridge-tunnel.service's own systemd unit just retries silently on ExitOnForwardFailure -- there is no visibility into how long it has been down. Ivan, 2026-09-24: file a small task for this and do it now.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A lightweight check (systemd timer or an addition to an existing periodic job) detects whether 127.0.0.1:2222 is accepting connections
- [x] #2 A prolonged outage (threshold, e.g. 60s+) is logged loudly (journalctl) so it is visible without an ad-hoc manual check -- matches CLAUDE.md's 'failures fail loudly and get recorded'
- [x] #3 The check itself never touches the mini or the bridge -- a plain TCP connect to the local forwarded port, so it cannot itself contribute load or false-trigger the phone
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented app/wa/tunnel_watch.py: a plain TCP connect probe of the local forwarded port (WA_BRIDGE_LOCAL_PORT, 18793), state tracked in a small JSON file (failure streak start + whether already alerted), an ERROR journal line once the streak crosses WA_TUNNEL_WATCH_ALERT_AFTER_SEC (default 60s) fired exactly once per outage, and an INFO RECOVERED line (naming the outage duration) on the next successful probe after an alerted outage -- a blip that self-heals under the threshold stays silent both ways, so the journal only carries real signal. deploy/pflege-wa-tunnel-watch.service + .timer (30s interval, mirrors the existing pflege-wa-followups timer pattern), installed and enabled live on tasker-dispatcher-01 (systemctl enable --now). 7 new unit tests (tests/test_wa_tunnel_watch.py): streak recording, single-alert-per-outage, recovery logging, silent recovery under threshold, and the probe itself proven to open/close a real local socket without sending any bytes (AC#3).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/tunnel_watch.py polls the phone rail's local forwarded port every 30s and logs loudly (ERROR) the first time an outage crosses 60s, then an INFO RECOVERED line naming how long it lasted -- closes the blind spot from tonight's 36-minute silent tunnel outage. Installed and running live; verified with 7 unit tests.
<!-- SECTION:FINAL_SUMMARY:END -->
