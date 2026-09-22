---
id: TASK-143
title: Pull-based inbound relay and the mini deployment of the executor
status: Done
assignee: []
created_date: '2026-09-21 09:54'
updated_date: '2026-09-21 13:24'
labels: []
dependencies:
  - TASK-142
priority: high
type: feature
ordinal: 151000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An ingress tunnel into the VPS is not available, so nothing on the mini may connect to our server. The mini therefore keeps a durable local inbound outbox with a monotonic cursor and serves it on loopback; our side drains it over an ssh connection initiated from the VPS and feeds each item to POST /api/wa/bridge-webhook. The cursor is acked only after our server accepted the item, so a crash redelivers instead of losing a candidate reply.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 a watcher thread inside the executor polls the handset for inbound and appends every new message to the ledger inbound table with a heartbeat visible in /v1/health
- [x] #2 bridge/relay_pull.py runs on the VPS, reaches the mini over ssh, and posts each item as a Meta-shaped envelope to the bridge webhook
- [x] #3 the cursor advances only after the server accepted the item
- [x] #4 the executor runs on the mini as a systemd --user unit with Restart=always and survives a restart
- [x] #5 deploy/wa-bridge/ holds the mini unit, the VPS relay unit template and INSTALL.md
- [x] #6 inbound ids are collision-free: two identical texts from two numbers in the same minute get two distinct ids
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Extend bridge/ledger.py inbound table with a watcher, add bridge/watcher.py, bridge/envelope.py, bridge/relay_pull.py, deploy/wa-bridge/*.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Shipped and running on the mini: pflege-wa-bridge.service (systemd --user, Restart=always, enabled, linger already on). Verified active after kill -9 and after an explicit restart; the inbound outbox survived both.

A live bug the deploy caught before anything was delivered: WhatsApp posts TWO notification records per message -- the MessagingStyle one and a plain group-summary record carrying the same android.text. The first cut read both, and stamped the summary (which has no per-message timestamp) with a clock read at parse time, so its id changed every minute: the journal shows 'watcher: 1 new inbound' at 10:10, 10:11, 10:12 and 10:13 for one message. Ivan would have been answered once a minute. Fixed in bridge/inbound.py: messages come only from MessagingStyle lines, android.text is read only for a title with no MessagingStyle record anywhere in the dump, its timestamp is the record's own mCreationTimeMs, and a record with no timestamp at all is reported as unresolved rather than stamped with now. Covered by three tests in tests/test_bridge_relay.py.

AC#2 (relay delivers to the webhook) is built and unit-tested but not yet proven end to end: POST /api/wa/bridge-webhook still answers 404 on 8502 and the two shared secrets are not in .env. The relay unit is a template in deploy/wa-bridge/ and was deliberately NOT installed on the VPS.

Dry run of the whole pull path on real data, 2026-09-21 10:21 UTC: relay opened its ssh -L from the VPS, fetched the one outbox item off the mini, built the Meta envelope, POSTed it to a stand-in webhook on 127.0.0.1 with X-Pflege-Bridge-Token and X-Bridge-Delivery-Id, got an accepted_summary back, advanced a throwaway cursor, and moved nothing on the second pass. The real cursor file is still at 0, so the message is still queued for the live relay. Round-trips over the Wi-Fi link: GET /v1/health 234-563 ms, POST /v1/messages (refused before the phone) 120-461 ms, one full outbox drain 1.1 s including opening the tunnel.

Also added deploy/wa-bridge/pflege-wa-bridge-tunnel.service: the forward is its own unit because outbound (WA_BRIDGE_URL) must not depend on the inbound relay being up. bridge/relay_pull.SshForward borrows a forward that is already on the port and never kills one it did not start.

Offline suite after all of it: 1894 passed, 127 skipped.

AC#2 proven live 2026-09-21. pflege-wa-bridge-relay.service is installed and running on tasker-dispatcher-01 (ExecStart python -m bridge.relay_pull, EnvironmentFile .env + rail.env + relay.env). Evidence, read back afterwards: relay.sqlite relay_delivered holds outbox item 1, inbound id wab.i.bd3bf1b0..., status accepted, delivered_at 2026-09-21T11:35:05.377Z, and relay_cursor advanced to position 1 with the same timestamp; pflege-wa.service journal shows POST /api/wa/bridge-webhook 200 OK at 11:35:05 from loopback. The harness answered that message over the rail: the mini ledger holds the two answering bubbles at 11:35 and 11:36 in state sent with tick Zugestellt. So the whole pull path -- watcher, outbox, ssh -L, envelope, webhook, ack -- ran on a real message, not a dry run.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The phone rail's inbound half is live: the executor's watcher appends to the ledger outbox, bridge/relay_pull.py drains it from the VPS over an ssh -L it borrows rather than owns, posts each item as a verbatim Meta envelope to POST /api/wa/bridge-webhook and acks only after our server accepted it. Verified on a real candidate message on 2026-09-21 at 11:35:05 UTC (relay_delivered accepted, cursor at 1, webhook 200, two answering bubbles sent with delivery ticks).
<!-- SECTION:FINAL_SUMMARY:END -->
