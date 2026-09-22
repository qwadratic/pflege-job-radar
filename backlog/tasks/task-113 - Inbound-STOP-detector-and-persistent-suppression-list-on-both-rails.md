---
id: TASK-113
title: Inbound STOP detector and persistent suppression list on both rails
status: To Do
assignee: []
created_date: '2026-09-21 01:20'
updated_date: '2026-09-22 06:08'
labels:
  - wa-transport
dependencies: []
references:
  - /home/claude/plans/2026-09-20-wa-home-transport-plan.md
priority: high
type: feature
ordinal: 121000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan M9, promoted by decision-8 (2026-09-21) to first and BLOCKING for any campaign on either rail. Not a milestone, a gate.

Framing corrected by the read-only investigation (/home/claude/plans/2026-09-21-macmini-revision.md). The original text said the STOP detector had to be built. It already exists: app/wa/slots.py:52,:97 is_stop is a whole-word detector, reached from brain.py, luna_brain.py and api.py, and app/wa/store.py documents it as checked before every send. That half is done and is transport-independent.

What is missing is the SUPPRESSION TABLE. app/wa/store.py has 13 tables and none of them is one, so an opt-out is remembered per thread and nowhere else. Campaign-level opt-out is worse: campaign.py marketing_opt_out reads Meta signals only -- a user_preferences webhook and failed status code 131050 -- which a phone rail can never produce. Moving transports without a persistent list silently deletes our only campaign-level opt-out detector.

The colleague lane has neither: a grep for stop|opt.?out|suppress|abmeld|dsgvo|consent|einwillig over his *.py and *.json returns two hits, one of which is log("daemon stop").

Ivan re-confirmed on 2026-09-21, after being shown the case against it, that cold first contact stays on the phone rail. That is what makes this blocking rather than a follow-up: cold first contact is exactly the traffic that draws blocks and reports, and there is no opt-out mechanism on the rail carrying it. Legal floor, not a nicety: 7 Abs. 3 UWG and 174 TKG both require a free and easy opt-out.

This task builds the detector coverage and the persistent list on our side. TASK-137 makes it cross-lane and adds the salted-digest export plus the old-system import. Both are blocking before any first touch on either rail.

Forged Meta numeric codes stay out of scope and rejected: bridge-origin failures use our own wab-* slugs.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 An inbound message matching Stopp, stop, keine Nachrichten mehr, kein Interesse or abmelden writes a persistent suppression row keyed by phone, storing the verbatim matched text and the timestamp
- [x] #2 Matching is case-insensitive and diacritic-folded, and is evaluated on every inbound message regardless of which rail delivered it
- [x] #3 Every outbound path consults the suppression list before sending: conversational reply, follow-up nudge and campaign send, on the Meta rail and the bridge rail alike
- [ ] #4 marketing_opt_out reads the suppression list instead of Meta error code 131050
- [ ] #5 Phones already recorded as opted out via 131050 are backfilled into the suppression list, so no existing opt-out is lost by the switch
- [x] #6 Offline tests cover detection of each listed phrase, suppression on each of the three send paths, and that a suppressed phone is never sent to again on either rail
- [ ] #7 docs/whatsapp.md documents the STOP vocabulary, where the list lives and how a suppression is inspected
- [ ] #8 No campaign can start on either rail while the suppression store is missing, empty or unreachable: it refuses loudly rather than sending and logging a warning
- [x] #9 The existing whole-word detector in slots.py is reused rather than reimplemented, and a test proves Stopfen and Intensivstation still do not fire it
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Audit 2026-09-22: substantially built (app/wa/suppression.py, 129 lines) but genuinely NOT complete -- correctly stays To Do. What exists: wa_suppressions table (store.SCHEMA), suppress()/is_suppressed()/assert_not_suppressed(), keyed by phones.canonicalize_phone, first-write-wins. is_stop() reused verbatim from slots.py (AC#9), STOP triggers suppress() at api.py:896 on any inbound regardless of rail (AC#1/#2 -- _fold in slots.py case/diacritic-folds, _contains is whole-word for single tokens). Two choke points, both raising SuppressedRecipient(MetaError, 403): api.send_and_record (api.py:1027, covers webhook reply/catch-up/media-ack/reopen-template/followups nudge on either rail) and campaign.send_one (campaign.py:672, the one send path that bypasses api._send). tests/test_wa_suppression.py: 28 tests, all passing, covering detection, cross-rail persistence, the campaign and followup choke points, and identity canonicalization (AC#3/#6). WHAT IS NOT BUILT, stated explicitly in suppression.py's own docstring ('NOT HERE, ON PURPOSE (TASK-137, later)'): AC#4 (marketing_opt_out still reads Meta signals/131050 directly, campaign.py:384-386, not the suppression list); AC#5 (no backfill of phones already opted out via 131050 into wa_suppressions). Also not found anywhere: AC#7 (docs/whatsapp.md does not document the STOP vocabulary, where the table lives, or how to inspect it -- it only namechecks TASK-113 as a still-blocking prerequisite, docs/whatsapp.md:79,125, which is itself now stale prose since the detector+table are built); AC#8 (no campaign-start gate refuses when wa_suppressions is missing/empty/unreachable -- grepped app/wa/luna/campaign.py's main()/cmd_send path, no such check exists; a campaign today would run fine against a suppression table nobody ever populated). Full offline suite green: 2312 passed.
<!-- SECTION:NOTES:END -->
