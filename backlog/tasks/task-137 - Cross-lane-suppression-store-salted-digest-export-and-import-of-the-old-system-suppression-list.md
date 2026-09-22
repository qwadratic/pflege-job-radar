---
id: TASK-137
title: >-
  Cross-lane suppression store, salted-digest export, and import of the old
  system suppression list
status: To Do
assignee: []
created_date: '2026-09-21 09:10'
labels:
  - wa-transport
dependencies:
  - TASK-113
references:
  - /home/claude/plans/2026-09-21-macmini-revision.md
priority: high
type: feature
ordinal: 145000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
BLOCKING for any campaign on either rail, together with TASK-113. Not a milestone, a gate: no first touch goes out on the phone rail or the Meta rail until both are live.

TASK-113 builds the detector and the list on our side. This task makes it CROSS-LANE, which is the part that actually matters now that two independent senders can reach one candidate on one handset.

What exists and what does not:
- Ours: a whole-word STOP detector (app/wa/slots.py:52,:97 -> brain.py, luna_brain.py, api.py), documented in store.py as checked before every send. There is NO suppression table -- app/wa/store.py has 13 tables and none is one.
- Theirs: nothing. A grep for stop|opt.?out|suppress|abmeld|dsgvo|consent|einwillig over their *.py and *.json returns two hits, one of which is log("daemon stop").
- The old system: sales_brain.suppression_list, filtered to channel_type in (whatsapp, phone, sms), imports ZERO rows today -- all 378 are email. That is exactly why it ships before anyone has to remember it: the import is trivial now and impossible to retrofit after the first WhatsApp opt-out is lost.

Suppression travels between lanes as SALTED SHA256 DIGESTS, never as a plaintext do-not-contact list. Their home directory is shared with a root-installed Cursor cloud agent worker; a plaintext list of people who told us to go away is the worst possible file to leave there.

Single writer: the ledger lives on our side. The digest export is what the other lane reads.

Ivan re-confirmed on 2026-09-21 that cold first contact stays on the phone rail, which is what makes this blocking rather than a follow-up (decision-8 item 6).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A suppression table exists in data/wa.sqlite with a single writer on our side, keyed by phone, storing the verbatim matched text, the source lane and the timestamp
- [ ] #2 Every outbound path on both rails consults it before sending: conversational reply, follow-up nudge and campaign send
- [ ] #3 The export emits salted sha256 digests only; a test asserts no plaintext phone number appears in the exported artefact
- [ ] #4 sales_brain.suppression_list is imported read-only, filtered to channel_type in (whatsapp, phone, sms), and the import records how many rows it found including zero
- [ ] #5 A suppression recorded by either lane blocks the other lane, proven by a test that suppresses via the phone rail and asserts the Meta rail refuses to send
- [ ] #6 Campaign start refuses outright when the suppression store is empty or unreachable, rather than sending and logging a warning
- [ ] #7 docs/whatsapp.md documents where the list lives, how a suppression is inspected, and that it gates both rails
<!-- AC:END -->
