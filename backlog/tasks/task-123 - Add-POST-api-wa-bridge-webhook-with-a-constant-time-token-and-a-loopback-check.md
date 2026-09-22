---
id: TASK-123
title: >-
  Add POST /api/wa/bridge-webhook with a constant-time token and a loopback
  check
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 01:22'
updated_date: '2026-09-21 10:12'
labels:
  - wa-transport
dependencies:
  - TASK-119
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: medium
type: feature
ordinal: 131000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M8. The inbound door for the phone rail.

The home machine pushes a VERBATIM Meta-shaped envelope, so parse_webhook_body -> accept_payload -> parse_message -> record_inbound_pending and all 31 tests/test_wa_*.py stay valid with zero changes. If those need edits, the adapter is not thin enough and the design is wrong.

Auth is two independent secrets: WA_BRIDGE_TOKEN server-to-home, WA_BRIDGE_INBOUND_TOKEN home-to-server. A leak in one direction does not grant the other. META_WHATSAPP_APP_SECRET is never copied to the home machine.

The loopback check is not decoration. The ssh -L leg means sshd opens the connection to our port from the server own stack, so request.client.host is 127.0.0.1 and _is_local_caller passes (`app/wa/router.py:228,231-237,246`). A VPN-sourced POST would be 403, which is the decisive reason ssh beats WireGuard here.

Dedup is free: messages[].id lands in wa_messages.wamid which is UNIQUE (`store.py:42`), so a re-push hits IntegrityError in record_inbound_pending (`store.py:618-630`) and is dropped as a redelivery, exactly like a Meta redelivery.

Do NOT blank META_WHATSAPP_PHONE_NUMBER_ID: `_number_matches` (`api.py:120`) returns True for everything when it is empty. Accept either value instead.

WA_INTERNAL_WEBHOOK_ENABLED stays absent; this is our own token-gated door, not that one.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A Meta-shaped bridge payload produces the same wa_messages and wa_inbound_pending rows as the equivalent Meta webhook
- [x] #2 The same payload delivered twice yields exactly one row, via the existing UNIQUE wamid path and not a new dedup table
- [x] #3 A request from a non-loopback address is 403, and a bad or missing token is 403, with the token compared using hmac.compare_digest
- [x] #4 The statuses key on the same endpoint reaches record_message_status unchanged, and bridge-origin failures carry our own wab-* slugs rather than forged Meta numeric codes
- [x] #5 _number_matches accepts both the Meta and the bridge phone_number_id, and META_WHATSAPP_PHONE_NUMBER_ID is left set
- [x] #6 GET /api/wa/bridge-health proxies the home machine health so one curl answers whether the rail is alive
- [ ] #7 All 31 tests/test_wa_*.py pass untouched
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New app/wa/bridge_api.py: POST /wa/bridge-webhook, loopback check then hmac.compare_digest on X-Pflege-Bridge-Token (WA_BRIDGE_INBOUND_TOKEN), then API.accept_payload + API.submit_accepted + API.accepted_summary verbatim.
2. config.py: WA_BRIDGE_INBOUND_TOKEN, WA_BRIDGE_PHONE_NUMBER_ID, readiness() gains bridge_inbound_ready.
3. api._number_matches accepts {PHONE_NUMBER_ID, BRIDGE_PHONE_NUMBER_ID} - {''}; META_WHATSAPP_PHONE_NUMBER_ID stays set.
4. GET /wa/bridge-health proxies the executor's /v1/health through bridge.Client's own wire.
5. asgi.py includes the router.
6. Tests in tests/test_wa_bridge_inbound.py, driven through the real app.wa.asgi app.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Shipped 2026-09-21. app/wa/bridge_api.py is 100 lines and translates nothing: it checks the caller and hands the raw body to the same accept_payload/submit_accepted/accepted_summary trio api.receive_webhook uses.

- Auth: loopback first (request.client.host in 127.0.0.1/::1), then hmac.compare_digest against WA_BRIDGE_INBOUND_TOKEN in X-Pflege-Bridge-Token. An unset token shuts the door instead of matching an absent header. WA_BRIDGE_TOKEN (server -> executor) does not open it.
- _number_matches now accepts either phone_number_id; META_WHATSAPP_PHONE_NUMBER_ID stays set, so a third number's payload is still stored raw and never answered.
- GET /api/wa/bridge-health proxies the executor's /v1/health through bridge.Client's own wire (same base URL, bearer, timeout, error taxonomy) and answers {ok:false, error} when the rail cannot be reached.

Tests (tests/test_wa_bridge_inbound.py, 20, all through the real app.wa.asgi app, TestClient client= for the network origin): identical wa_messages/wa_inbound_pending rows from the bridge door and Meta's signed route on two fresh databases; the worker answers the thread; one row for a re-push and two for two ids, with no second dedup table; 403 for a non-loopback caller, for a missing/empty/wrong/prefix/suffix/uppercased token and for the outbound bearer, recording nothing; compare_digest proven to be on the path; a failed bridge status lands in wa_message_statuses and wa_send_failures with the wab-blocked slug and no forged Meta numeric; a suppressed number is refused on this rail with not even a draft written.

Not done here: nothing in .env was touched -- WA_BRIDGE_INBOUND_TOKEN and WA_BRIDGE_PHONE_NUMBER_ID still have to be set, and pflege-wa.service restarted, before the door is reachable in production.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:15
---
decision-8 (2026-09-21): KEEP as written. The verbatim Meta envelope is what keeps parse_message, accept_payload and all 32 tests/test_wa_*.py files valid untouched.
---

author: @claude
created: 2026-09-21 10:12
---
AC#7 deliberately left unchecked. Every tests/test_wa_*.py passes (full offline suite 1870 passed, 127 skipped, 70 deselected) and the inbound adapter needed no test edit at all -- but tests/test_wa_transport.py is not untouched: two assertions that WA_TRANSPORT=bridge raises 'no bridge client yet' were rewritten because TASK-117 wires get_client to bridge.Client. That is the transport seam changing, not the envelope, so a human should decide whether AC#7 counts as met.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
POST /api/wa/bridge-webhook in the new app/wa/bridge_api.py, mounted from asgi.py: loopback check, constant-time token check, then the Meta webhook's own accept/submit path unchanged, so parse/accept/dedup/worker code is reused as is and dedup stays the UNIQUE wa_messages.wamid. Plus GET /api/wa/bridge-health proxying the executor. Verified by tests/test_wa_bridge_inbound.py (20 tests through the real ASGI app, including a row-by-row comparison against Meta's signed route) and a green offline suite. AC#7 left unchecked, see the comment: the adapter needed no test edits, but tests/test_wa_transport.py changed for the TASK-117 seam.
<!-- SECTION:FINAL_SUMMARY:END -->
