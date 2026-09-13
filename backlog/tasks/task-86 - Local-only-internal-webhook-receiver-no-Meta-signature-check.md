---
id: TASK-86
title: Local-only internal webhook receiver (no Meta signature check)
status: Done
assignee: []
created_date: '2026-09-13 13:42'
updated_date: '2026-09-13 13:46'
labels: []
dependencies: []
ordinal: 86000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Revised router architecture per Ivan: the real system's existing webhook stays Meta's primary entry point. It gains a small check (is this phone already a known candidate in their DB?) and, for a brand-new lead only, forwards the original payload to a local endpoint on this harness -- same host, direct loopback call, no public exposure. Our side therefore does not need to re-verify Meta's signature (the real system already did, and this call never crosses the public internet) -- but it does need its own access control, since removing signature verification without any other check would let anything that can reach the port inject fake messages.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New endpoint (e.g. POST /api/wa/internal-webhook) processes a payload via the existing handle_payload() pipeline, unchanged -- no re-verification of a Meta signature
- [x] #2 Access is restricted to local-origin requests only (loopback check) AND a separate explicit opt-in config flag (default off) -- defense in depth, since dropping signature verification is a real access-control change, not just a convenience
- [x] #3 A non-local caller is rejected (403), even with the flag enabled
- [x] #4 The flag being off rejects every call regardless of origin, so a fresh/misconfigured deployment is closed by default
- [x] #5 Mounted in both app/wa/asgi.py and app/main.py, matching every other WA route
- [x] #6 Unit tests cover: local + enabled succeeds, local + disabled rejected, non-local + enabled rejected, payload actually reaches handle_payload
- [x] #7 Full offline suite stays green
- [x] #8 docs/whatsapp.md documents the intended real-system-side integration (what the other codebase would need to add) without implying this repo modifies that codebase itself
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/router.py: new POST /wa/internal-webhook, gated by C.INTERNAL_WEBHOOK_ENABLED (default off, WA_INTERNAL_WEBHOOK_ENABLED) AND _is_local_caller() (request.client.host in 127.0.0.1/::1) -- both must pass. No Meta signature check: the caller is a same-host process, not Meta, per the revised architecture where the real system stays Meta's primary webhook. Already mounted in both asgi.py and main.py since it's on the same router object as TASK-84's route-webhook. 5 new tests using Starlette TestClient's client= override to genuinely simulate loopback vs non-local origin end to end (not just unit-testing the helper function). Offline suite: 1138 passed, same 5 pre-existing unrelated failures. docs/whatsapp.md documents the intended real-system-side integration conceptually, explicitly NOT as a ready-to-apply patch -- see conversation notes: a recon subagent tasked with gathering the exact real-system integration point declined, raising a legitimate concern about authorization for redirecting real candidate data before any further work on that side proceeds. This task's own scope (the receiving half, in this repo) is unaffected and complete.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
The receiving half of the alternative router architecture: a local-only endpoint gated by both an explicit opt-in flag and a network-origin check, since dropping Meta signature verification is a real access-control change that needs its own explicit safeguard. Fully built, tested end-to-end (genuine loopback vs remote origin via TestClient), and documented. The real-system-side half (the small addition to their live webhook) is deliberately out of scope for this repo and was not drafted, pending an explicit authorization conversation.
<!-- SECTION:FINAL_SUMMARY:END -->
