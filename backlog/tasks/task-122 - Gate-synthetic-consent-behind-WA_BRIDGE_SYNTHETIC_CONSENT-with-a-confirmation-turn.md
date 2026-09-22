---
id: TASK-122
title: >-
  Gate synthetic consent behind WA_BRIDGE_SYNTHETIC_CONSENT with a confirmation
  turn
status: To Do
assignee: []
created_date: '2026-09-21 01:21'
updated_date: '2026-09-21 09:14'
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
- [ ] #1 WA_BRIDGE_SYNTHETIC_CONSENT defaults to ON (plan addendum item 5) and is validated with the same no-silent-default discipline as the other config names; an unparseable value raises at import
- [ ] #2 With the flag off, no typed reply can set anonymous_send_consent by any path; a test asserts this across ordinal, exact-title and keyword inputs
- [ ] #3 With the flag on, consent requires a tier-1 ordinal or tier-2 exact-title match PLUS an explicit confirmation turn; a prefix match or a keyword match never sets consent
- [ ] #4 When consent is set this way the verbatim typed token is stored in wa_messages.meta as the audit artefact
- [ ] #5 luna_brain.py and brain.py are not edited by this task
<!-- AC:END -->

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
