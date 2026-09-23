---
id: TASK-288
title: 'A failed phone send retries the whole Luna brain call, not just the send'
status: To Do
assignee: []
created_date: '2026-09-23 22:07'
labels:
  - whatsapp
  - reliability
dependencies: []
ordinal: 241000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Live incident, 2026-09-23 ~21:16-21:19 UTC: the mini bridge's phone lock was busy for 30s (reconcile_watcher: 'device_unavailable (503): phone lock busy for 30s'), causing several sends from the VPS to fail with bridge HTTP 500/504. app/wa/api.py::process_owed_turn / finish_inbound's retry path (catchup.py, every 3 min) re-runs the ENTIRE turn on each retry -- including a fresh, expensive claude -p brain call (app/wa/luna_brain.py::_live_reply) -- even though the reply had already been correctly composed and only the delivery (bridge send) failed. Effect observed live: two inbound messages from +436704048778 (Ivan's own UAT number) retried 11 and 8 times respectively over ~50 minutes, burning 19 of the 20 allowed WA_LUNA_MAX_CALLS_PER_HOUR (app/wa/config.py) calls for that phone -- a genuinely new inbound turn then got skipped_rate_cap (app/wa/api.py:884) and Ivan got no reply at all for close to an hour, self-healing only once the oldest burned calls aged out of the 1-hour rolling window (app/wa/store.py::count_recent_luna_calls). The rate cap itself worked as designed; the problem is that a pure delivery failure (compose succeeded, send did not) consumes the same budget as a full compose, so a short bridge hiccup can starve out real conversation for up to an hour afterward.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A send-only failure (brain composed successfully, bridge/executor send failed) retries the send without re-invoking the Luna brain, OR the retry accounting excludes calls whose failure was provably delivery-side, not compose-side
- [ ] #2 A reproduction test exists: brain call succeeds, simulated send fails N times, asserts the brain is invoked once (or a bounded small number of times), not N times
- [ ] #3 WA_LUNA_MAX_CALLS_PER_HOUR still protects against a genuine runaway/abusive conversation -- this fix narrows what counts against it, it does not raise or remove the cap
<!-- AC:END -->
