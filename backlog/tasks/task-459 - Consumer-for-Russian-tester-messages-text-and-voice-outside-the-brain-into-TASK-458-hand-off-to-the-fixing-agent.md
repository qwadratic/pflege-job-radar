---
id: TASK-459
title: >-
  Consumer for Russian tester messages: text and voice outside the brain into
  TASK-458, hand-off to the fixing agent
status: To Do
assignee: []
created_date: '2026-10-10 10:32'
labels:
  - whatsapp
  - wacli
  - agent-notes
dependencies:
  - TASK-458
priority: high
type: feature
project: whatsapp
ordinal: 340000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-10: on the wacli rail only German text from 10 Oct 2026 reaches the brain. Russian text is already stored with meta.outside_brain=not_german but nothing reads it. A Russian voice note has no caption, so the door cannot tell its language and it goes to the brain today; that is a hole against the rule 'Russian stays outside the brain'. The testers' Russian is feedback on the bot. A new consumer, not the pflege-clawl numbering session (offline) and not TASK-303's note worker, writes it into the one pre-made card TASK-458 and hands it to the agent that fixes and opens the PR. Related: TASK-210 (voice transcription for candidates), TASK-400 (voice language detected and stored as text marked voice), TASK-303 (the existing operator-note hand-off to the wa-harness session). Open decision for the builder to raise with Ivan before building the hand-off: reuse TASK-303's SendMessage hand-off or start the fixing agent differently.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A Russian voice note from a whitelisted tester is transcribed and its language decided by a Haiku-tier model before the door; Russian goes to the stream, German goes to the brain, anything undecidable fails loudly and is recorded, never guessed
- [ ] #2 Every wa_messages row with meta.outside_brain=not_german from a whitelisted tester is claimed by the consumer exactly once; a retry never duplicates an entry
- [ ] #3 The consumer appends to TASK-458 only, by code, with verbatim Russian, English decoding, tester label and wa_messages id; it never creates a task and never touches the card's status; a missing TASK-458 fails loudly
- [ ] #4 No message of this stream is ever shown to the brain, and the brain's German path is unchanged (golden and replay tests stay green)
- [ ] #5 The hand-off to the fixing agent carries no tester-derived text, only the entry key and the commands to read it
- [ ] #6 Offline tests cover claim, idempotency, missing card, Russian voice and German voice routing with faked transcription, faked language model and faked backlog
<!-- AC:END -->
