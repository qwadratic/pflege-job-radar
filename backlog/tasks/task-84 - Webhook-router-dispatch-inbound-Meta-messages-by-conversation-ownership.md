---
id: TASK-84
title: 'Webhook router: dispatch inbound Meta messages by conversation ownership'
status: In Progress
assignee: []
created_date: '2026-09-13 13:13'
updated_date: '2026-09-13 13:17'
labels: []
dependencies: []
ordinal: 84000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-75 built the ownership decision (wa_ownership, route_decision) but explicitly left out the piece that acts on it -- Meta only supports one webhook URL, so something has to receive the real call and dispatch by phone. Build that dispatcher as safe, testable, offline code. Actually pointing Meta's real webhook at it is a separate, external, production-infrastructure change (needs coordination with the real system's team) that this task does not do.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New module receives a verified Meta webhook payload, splits it by app.wa.routing.route_decision() per phone (a payload can carry messages for multiple phones/owners at once)
- [x] #2 Messages owned by 'us' are processed via the existing app.wa.api.handle_payload(), unchanged
- [x] #3 Messages owned by 'them' are forwarded, byte-for-byte reconstructed and re-signed with the shared Meta app secret, to a configured real-system webhook URL (WA_REAL_SYSTEM_WEBHOOK_URL) so their own signature check still passes
- [x] #4 Unset WA_REAL_SYSTEM_WEBHOOK_URL with a 'them'-owned message present raises loudly rather than silently dropping it
- [x] #5 Exposed as a new route, additive only -- the existing POST /api/wa/webhook is untouched and still exercised by all current tests
- [x] #6 Unit tests cover: all-us payload, all-them payload, mixed payload split correctly, missing forward URL, forward signature is verifiably correct
- [x] #7 Full offline suite stays green
- [x] #8 NOT in scope: registering this route as Meta's actual webhook URL, or otherwise touching real production configuration -- that is a separate, explicitly-confirmed step
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/router.py: route_webhook(raw_body, signature_header, meta_client=None, forward=None) -- verifies signature (same as api.wa_webhook), splits the payload by routing.route_decision() per message's phone, processes 'us' via the existing api.handle_payload() unchanged, forwards 'them' as a reconstructed+re-signed payload (fresh HMAC over the actual bytes sent, since the split body differs from the original raw bytes) to WA_REAL_SYSTEM_WEBHOOK_URL. Missing URL with a them-message present raises loudly. New route POST /api/wa/route-webhook, mounted in both app/wa/asgi.py and app/main.py (same two-doors pattern as the existing webhook), NOT registered anywhere as Meta's actual URL. 9 new tests covering signature checks, all-us, all-them, mixed split, missing-URL, forwarded-signature-correctness, empty payload. Offline suite: 1122 passed, same 5 pre-existing unrelated failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The dispatch layer TASK-75 deliberately deferred: a payload can now be split per-message by ownership and routed accordingly, with 'them' messages forwarded re-signed so the real system's own signature check still passes. Fully built and tested as code; deliberately does NOT touch Meta's actual webhook configuration -- that remains a separate, explicit, external step for whoever owns that Meta app.
<!-- SECTION:FINAL_SUMMARY:END -->
