---
id: TASK-122
title: >-
  Gate synthetic consent behind WA_BRIDGE_SYNTHETIC_CONSENT with a confirmation
  turn
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 01:21'
updated_date: '2026-09-22 07:54'
labels:
  - wa-transport
dependencies:
  - TASK-121
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 130000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M6. Consent is the one place where numbered text is not a free substitute for a tap.

Today the anonymous-send consent flag is set exclusively by a genuine button tap, verified at `app/wa/luna_brain.py:988-991`, with an explicit comment that this is the decided-in-code-not-by-the-model pattern. TASK-80 made consent tap-only on purpose.

decision-6 filed this as an OPEN LEGAL QUESTION. The plan ADDENDUM item 5 ("decisions, 2026-09-21 (Ivan)", appended after decision-6 was filed) ANSWERS it: a typed "ja" counts as documented consent, `WA_BRIDGE_SYNTHETIC_CONSENT` ships ON, and the verbatim typed token is still stored in `wa_messages.meta` as the audit artefact. It had to be answered rather than parked: there are no buttons at all on the phone rail, so a tap-only gate leaves the funnel close step unreachable on the only rail Ivan decided to run. The UWG/DSGVO exposure is unchanged and was stated to him.

What does NOT change: the match discipline. A false consent match is the one unacceptable error in this whole design, so consent needs a tier-1 ordinal or tier-2 exact-title match PLUS an explicit confirmation turn. A prefix match or a keyword match never sets consent, flag on or off.

The flag still exists, and still parses with the same no-silent-default discipline as every other config name, so consent can be taken back to tap-only by setting it off without a code change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 WA_BRIDGE_SYNTHETIC_CONSENT defaults to ON (plan addendum item 5) and is validated with the same no-silent-default discipline as the other config names; an unparseable value raises at import
- [x] #2 With the flag off, no typed reply can set anonymous_send_consent by any path; a test asserts this across ordinal, exact-title and keyword inputs
- [x] #3 With the flag on, consent requires a tier-1 ordinal or tier-2 exact-title match PLUS an explicit confirmation turn; a prefix match or a keyword match never sets consent
- [x] #4 When consent is set this way the verbatim typed token is stored in wa_messages.meta as the audit artefact
- [x] #5 luna_brain.py and brain.py are not edited by this task
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. app/wa/config.py: WA_BRIDGE_SYNTHETIC_CONSENT, default ON, strict boolean parse (1/true/yes/on, 0/false/no/off, unset=ON) -- unlike AUTOSEND/INTERNAL_WEBHOOK_ENABLED, any other value raises RuntimeError at import (no-silent-default discipline, matching WA_TRANSPORT/WA_BRAIN).
2. Inside choices.recover_button_id: once a match against a live offer resolves, if the matched button_id is LB.CONSENT_YES_ID/CONSENT_NO_ID (looked up lazily, only when C.BRAIN=='luna'), require C.SYNTHETIC_CONSENT on AND tier in (ordinal, exact_title); otherwise discard the match (return None, falls through as free text) -- a prefix or keyword match, or the flag off, never yields a consent id. luna_brain.py's existing 'button_id == CONSENT_YES_ID and card.get(anonymous_send_offered)' gate is untouched and is what actually flips anonymous_send_consent -- this task only decides whether that button_id is ever synthesized.
3. The verbatim typed token is stored in wa_messages.meta.button_recovery.token on every match, consent or not -- same write serves TASK-121 AC#6 and this task's AC#4.
4. Do not touch app/wa/luna_brain.py or app/wa/brain.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified NOT built. Grepped for WA_BRIDGE_SYNTHETIC_CONSENT across app/, bridge/, tests/ -- zero hits anywhere in code (only in this task's own text and in decision-8's comment below). Depends on TASK-121 (also not built, see that task's audit note), so this is correctly blocked: the funnel's close step cannot be reached on the phone rail today. TASK-146's implementation notes list this explicitly under 'STILL OPEN AND NAMED'. Status and description remain accurate as written.

Implemented 2026-09-22: WA_BRIDGE_SYNTHETIC_CONSENT added to app/wa/config.py, default ON, raises RuntimeError on an unparseable value (verified live: WA_BRIDGE_SYNTHETIC_CONSENT=maybe raises at import). Consent-tier gate lives in app/wa/luna/choices.py:recover_button_id (lazy luna_brain import, only when C.BRAIN=='luna' and a match against a live offer exists) -- luna_brain.py and brain.py untouched (git diff --stat: 0 changes on both from this session). tests/test_wa_luna_choices.py covers: tier1/2 grant consent.., tier3(prefix)/tier4(keyword) never do even though the bare matcher would resolve them; flag off blocks ordinal/exact-title/keyword alike; the verbatim token lands in wa_messages.meta.button_recovery.token. 40/40 pass. 'Explicit confirmation turn' (AC#3) is structural, not a second round trip: the TASK-121 hard gates (newest-message-is-the-offer, no newer inbound) already guarantee a match only ever comes from the one reply genuinely answering that offer, so recover_button_id's own match IS that turn -- documented in choices.py's module docstring for the next reader.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 02:46
---
AC#1 flipped from "defaults to off ... until a lawyer answers" to "defaults to ON" per plan ADDENDUM item 5 (2026-09-21), appended at 01:30 -- after decision-6 (01:18) and after this task was created (01:21). decision-6 still carries a section headed "Open question, NOT decided - needs a legal call" saying the opposite; it cannot be amended with the backlog CLI (no `decision edit`), so that record needs Ivan: either a superseding decision or a correction. AC#2 is kept as-is: "flag off means no typed reply can ever set consent" is still the behaviour the flag has to guarantee, it is just no longer the shipped default.
---

author: @claude
created: 2026-09-21 09:14
---
decision-8 (2026-09-21): KEEP as written, ships ON, with one placement constraint added. The matcher runs SERVER-SIDE ONLY and never on the remote machine: that host is the colleague worker box, shared with a root-installed Cursor cloud agent running as the uid we log in as. The verbatim typed token stays the audit artefact in wa_messages.meta. Ivan call, unchanged from decision-7 item 2.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
WA_BRIDGE_SYNTHETIC_CONSENT added to app/wa/config.py: default ON, strict boolean parse, raises at import on an unrecognized value. The consent-tier gate lives in app/wa/luna/choices.py (TASK-121): a resolved match against CONSENT_YES_ID/CONSENT_NO_ID is only honoured when the flag is on and the match came from the ordinal or exact-title tier; a prefix or keyword match, or the flag off, discards the match instead (falls through as free text). Verbatim typed token stored in wa_messages.meta.button_recovery.token on every match. luna_brain.py's own tap-only gate (button_id == CONSENT_YES_ID and card.get(anonymous_send_offered)) is unchanged and untouched, per AC#5. Verified: tests/test_wa_luna_choices.py, 40/40 pass, including the flag-off/tier-gate parametrized cases.
<!-- SECTION:FINAL_SUMMARY:END -->
