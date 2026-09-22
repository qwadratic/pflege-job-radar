---
id: TASK-121
title: Numbered-text button rendering and server-side button-id recovery
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 01:21'
updated_date: '2026-09-22 07:54'
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
- [x] #1 render(body, buttons) produces the numbered German form, and brain.py and luna_brain.py are not edited: their {id, title} dicts stay authoritative
- [x] #2 A table-driven test over CONSENT_BUTTONS and the brain.py question ladder resolves 1, 1., 1), ja, Ja gerne, JA GERNE, Pruefung and Prüfung to the right button id
- [x] #3 vielleicht returns None and reaches Luna as ordinary free text rather than being forced to a button
- [x] #4 The keyword map fires only on two-button offers, and two matching titles in one offer yield no match
- [x] #5 An offer older than the TTL yields no match, and an offer with a newer inbound after it yields no match
- [x] #6 Recovery is wired at the single process_owed_turn entry point so the webhook worker and catchup are both covered, and the matched tier is recorded in the message meta
- [ ] #7 Against a corpus of how candidates actually answered button questions in existing wa_messages history, at least 90 percent of genuine button-intent replies resolve, and there are ZERO false matches on the consent pair; below 90 percent the task stops and escalates rather than adding regex
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New module app/wa/luna/choices.py: fold() (casefold + German umlaut transliteration ae/oe/ue/ss + NFKD strip), a 3-tier matcher (ordinal / exact folded title / unique folded-prefix >=4 chars) plus a 2-button-only German yes/no keyword tier, all ambiguity-safe (>1 hit => no match).
2. Read the offer from the outbound wa_messages row api.py already writes (kind=buttons, meta.buttons) via the message immediately preceding the reply in that phone's history -- 'immediately preceding' is the hard gate for both 'newest outbound' and 'no newer inbound' at once. TTL 48h measured between the offer and the reply (not wall-clock now, so catch-up after an outage does not retroactively expire it).
3. Wire recover_button_id at the single process_owed_turn entry point (app/wa/api.py), one line: button_id = button_id or CH.recover_button_id(...) -- a genuine tap short-circuits it, both the webhook worker and catchup.py go through finish_inbound -> process_owed_turn so both are covered.
4. Record the matched tier + verbatim token on the inbound message's own meta (button_recovery) on every match -- doubles as TASK-122's consent audit artefact.
5. Do not touch app/wa/brain.py or app/wa/luna_brain.py; read CONSENT_YES_ID/CONSENT_NO_ID from luna_brain lazily, only inside recover_button_id and only when C.BRAIN=='luna'.
6. tests/test_wa_luna_choices.py: table-driven over the consent pair and the brain.py qualification ladder, ambiguous prefix, duplicate-title ambiguity, keyword-only-on-2-buttons, TTL, newer-inbound-retires-the-offer, tier+token recorded.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: verified NOT built. Grepped app/wa/ and bridge/ for an ordinal/title/prefix button-id recovery matcher -- the only hit is app/wa/bridge.py:537, a comment naming this exact gap ('an ordinal/title/prefix matcher shipped half-written would...'). process_owed_turn (api.py) does not recover a typed '1'/'ja' into a button id; TASK-146's own implementation notes list this task explicitly under 'STILL OPEN AND NAMED'. Consent therefore cannot complete on the phone rail today (send_buttons renders numbered text, per TASK-146, but nothing reads the numbered reply back). Status and description remain accurate as written.

Implemented 2026-09-22: app/wa/luna/choices.py (new), one-line wiring in app/wa/api.py:process_owed_turn (button_id = button_id or CH.recover_button_id(...)), no changes to app/wa/brain.py or app/wa/luna_brain.py (git diff --stat confirms zero). tests/test_wa_luna_choices.py: 40/40 pass (.venv/bin/python -m pytest tests/test_wa_luna_choices.py -q). tests/test_wa_harness.py (deterministic-brain end-to-end, exercises real button taps and free-text replies through the same process_owed_turn): 60/60 pass, unchanged -- no free-text reply in that suite accidentally resolves against a live button offer. AC#1-6 verified this way; AC#1's rendering half (send_buttons numbered text) predates this task (TASK-146, tests/test_wa_bridge_client.py::test_send_buttons_renders_the_titles_as_numbered_text, untouched). AC#6's 'both callers' confirmed by reading: app/wa/luna/catchup.py -> API.finish_inbound -> api.py:725 process_owed_turn; the webhook worker's _handle_one goes through the same finish_inbound. AC#7 (90pct corpus check against real candidate message history) NOT run: data/wa.sqlite on this host (tasker-dispatcher-01, the only wa.sqlite this deployment would use -- no WA_SQLITE_PATH override in .env) holds 3 threads, all is_test=1, 0 messages -- there is no real candidate history yet to check against (TASK-153's UAT broadcast, which this task blocks, has not run). Left unchecked rather than faked; re-run this corpus check once real replies exist post-UAT, per the task's own 90pct-or-escalate rule -- do not add regex to force it. Did not run the full test suite (Ivan's standing rule today: one full-suite run happens in Verify, owned elsewhere) -- app/wa/luna/grounding.py and tests/test_wa_luna_dialog_rules.py belong to a different in-flight workflow and were not touched or read for correctness.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-21 09:15
---
decision-8 (2026-09-21): KEEP as written. Buttons remain a platform impossibility on any phone rail rather than a gap; nothing in the mini investigation changes that.
---

author: @claude
created: 2026-09-22 07:54
---
6/7 ACs verified and checked; AC#7 (90pct real-history corpus check, zero false consent matches) cannot run yet -- data/wa.sqlite on this host has 0 messages (3 test threads only), and TASK-153 (the UAT broadcast this task blocks) has not happened yet. Left this task In Progress rather than Done so that gap stays visible; re-run the corpus check once TASK-153 produces real replies and then move this to Done, or say explicitly that AC#7 is a post-launch gate rather than a pre-launch blocker if that is the call.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added app/wa/luna/choices.py: server-side recovery of a typed phone-rail reply into the button id a genuine tap would have produced, 3 ambiguity-safe tiers (ordinal, folded exact title, folded unique prefix) plus a 2-button-only yes/no keyword tier, reading the offer from the outbound wa_messages row app/wa/api.py already writes. Wired at the single process_owed_turn entry point in one line, covering the webhook worker and catchup.py alike; brain.py and luna_brain.py untouched. Verified: tests/test_wa_luna_choices.py 40/40, tests/test_wa_harness.py 60/60 unchanged. AC#7's real-history corpus check could not run (no real candidate messages exist on this host yet); left unchecked with the reason on record rather than faked.
<!-- SECTION:FINAL_SUMMARY:END -->
