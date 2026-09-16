---
id: TASK-75
title: >-
  In-repo conversation ownership/routing (new leads to us, old leads stay with
  the real bot)
status: Done
assignee: []
created_date: '2026-09-13 11:14'
updated_date: '2026-09-13 11:52'
labels: []
dependencies: []
ordinal: 75000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan wants new WhatsApp conversations routed to our harness and existing/older ones to keep going to the real production bot, EXCEPT an old conversation we ourselves reopen with a template (TASK-70's reopen mechanism) -- that flips ownership to us from then on. Actually pointing Meta's webhook at a router is a production-infra change needing separate coordination/sign-off (out of scope here). This task is the safe, in-repo, reversible part: the ownership table, the decision function, and the automatic flip hook wired into our own reopen-template send path -- fully testable offline, no production change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 New table (own schema, same wa.sqlite) tracks phone -> owner ('us'/'them') with a reason and timestamp
- [x] #2 route_decision(conn, phone) function: unknown phone with no real-system record -> 'us'; known-to-real-system phone -> 'them' by default
- [x] #3 The real-system-existence check is a pluggable, explicitly-configured seam (env var, no default guess) -- absent config means the function refuses to guess rather than silently defaulting either way
- [x] #4 Sending a reopen template via our own _send_reopen_template durably flips that phone's owner to 'us', regardless of prior state
- [x] #5 Ownership is readable via an existing or new owner-gated endpoint
- [x] #6 Unit tests cover: new phone, known-real-system phone, unconfigured existence check, and the reopen-template flip
- [x] #7 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/routing.py: wa_ownership(phone, owner, reason, since). route_decision(conn, phone) is durable once decided (only flip_to_us_on_reopen may ever change it); for an undecided phone it calls _is_known_to_real_system(phone), a pluggable, EXPLICITLY-configured seam (WA_REAL_SYSTEM_PHONES_FILE, a plain newline-delimited file -- same generic-integration discipline as external_contacts.py, TASK-69) that raises loudly rather than guessing when unconfigured. flip_to_us_on_reopen() is wired into app/wa/api.py's _send_reopen_template -- the one explicit trigger that hands an existing conversation to us. New owner-gated GET /api/wa/ownership (added to auth.py's OWNER_READ_PREFIXES and test_auth.py's DENIED matrix -- same PII class as phone numbers elsewhere in this repo). Deliberately does NOT build the actual router in front of Meta's webhook -- that is a production-infrastructure change needing separate coordination, out of scope here by design. 10 new tests (9 routing + 1 api.py integration). Offline suite: 1109 passed, same 5 pre-existing unrelated failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
app/wa/routing.py gives the harness a durable, testable ownership decision (new lead -> us, existing real-system candidate -> them, our own reopen -> us from then on) without touching production webhook configuration at all -- that step is deliberately left for a separate, coordinated change. The existence check needed to tell 'new' from 'existing' is a pluggable, unnamed, explicitly-configured file rather than a hardcoded integration, matching this repo's established discipline for external-system references.
<!-- SECTION:FINAL_SUMMARY:END -->
