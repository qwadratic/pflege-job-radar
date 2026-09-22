---
id: TASK-121
title: Numbered-text button rendering and server-side button-id recovery
status: To Do
assignee: []
created_date: '2026-09-21 01:21'
updated_date: '2026-09-21 09:15'
labels:
  - wa-transport
dependencies:
  - TASK-117
  - TASK-120
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 129000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M6 plus the M7 numeric gate.

Buttons are impossible on a phone rail. Not unimplemented: neither the consumer app nor the free WhatsApp Business app has any UI to compose a reply button, so no accessibility service, adb macro or browser script can produce one. Decision-6 accepts this and replaces buttons with numbered text.

Rendering: body, then a numbered list of the button titles, then a line telling the candidate to answer with the number or the text. German, because it is a product string.

Recovery happens server side, in `process_owed_turn` (`app/wa/api.py:806`), the single shared pipeline for both the webhook worker and catchup.py. It already takes button_id and passes it to LB.turn at line 845, so one line at the top covers both callers. Do not synthesise interactive.button_reply on the wire: that would record a tap that never happened.

Matcher is three tiers in order: ordinal (1, 1., 1), (1)), then folded exact title (casefold plus NFKD diacritic fold so Pruefung and Prüfung both land), then unique title prefix of at least 4 characters. A small German keyword map (ja/gerne/ok/passt versus nein/nee/kein Interesse) applies ONLY to two-button offers.

Hard gates: match only against the newest outbound kind=buttons row with no newer inbound; two matches in one set means no match; TTL 48h; anything unmatched falls through to Luna as ordinary free text, which costs one turn and never stalls.

Read the offer from data `api.py:927-929` already writes (kind=buttons, meta with action and buttons). No wa_button_offers table: a second copy of the same fact would drift.

Consent is explicitly NOT covered here, see the synthetic-consent task.

The M7 corpus check uses real candidate message history. It runs as aggregate counts only; no candidate content is copied into the repo, the task or any report.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 render(body, buttons) produces the numbered German form, and brain.py and luna_brain.py are not edited: their {id, title} dicts stay authoritative
- [ ] #2 A table-driven test over CONSENT_BUTTONS and the brain.py question ladder resolves 1, 1., 1), ja, Ja gerne, JA GERNE, Pruefung and Prüfung to the right button id
- [ ] #3 vielleicht returns None and reaches Luna as ordinary free text rather than being forced to a button
- [ ] #4 The keyword map fires only on two-button offers, and two matching titles in one offer yield no match
- [ ] #5 An offer older than the TTL yields no match, and an offer with a newer inbound after it yields no match
- [ ] #6 Recovery is wired at the single process_owed_turn entry point so the webhook worker and catchup are both covered, and the matched tier is recorded in the message meta
- [ ] #7 Against a corpus of how candidates actually answered button questions in existing wa_messages history, at least 90 percent of genuine button-intent replies resolve, and there are ZERO false matches on the consent pair; below 90 percent the task stops and escalates rather than adding regex
<!-- AC:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:15
---
decision-8 (2026-09-21): KEEP as written. Buttons remain a platform impossibility on any phone rail rather than a gap; nothing in the mini investigation changes that.
---
<!-- COMMENTS:END -->
