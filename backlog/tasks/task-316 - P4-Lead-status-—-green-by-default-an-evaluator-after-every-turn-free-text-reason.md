---
id: TASK-316
title: >-
  P4: Lead status — green by default, an evaluator after every turn, free-text
  reason
status: To Do
assignee: []
created_date: '2026-09-26 08:48'
updated_date: '2026-10-07 15:11'
labels:
  - lead-state
dependencies: []
priority: medium
project: whatsapp
ordinal: 4
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The follow-up system, and later a dashboard of leads that need intervention, need a per-candidate status. TASK-299 specified five fixed statuses set only by operators or coded rules. Ivan revised that design on 2026-09-25.

**Revised design** (supersedes TASK-299's AC1-AC2)
- A candidate is green after a broadcast.
- After every turn an evaluator answers one question: is there a reason to leave green, and which one?
  - The reason is free text, not a fixed list.
  - Statuses have no order; green can move to any non-green.
- Our reactions (skip / pause / red / mute) are what we do in response. They are not reasons.
- The evaluator model is chosen on the P1 bench (Haiku vs others).
- Yellow flags from P2 (missing data) feed the evaluator.
- A dashboard over these columns comes later.

**Still valid from TASK-299**
- The status is read by every send path.
- Mute is the only status that sends nothing.
- Pause, red and skip keep their reaction semantics.
- STOP / opt-out means mute, and campaign sends must read it. Today campaign.py's marketing_opt_out does not read wa_suppressions.

**Open**
- How the evaluator's reason maps to a reaction.

Folded here: TASK-299. Its full text is kept in the archive.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A status column on the candidate defaults to green after a broadcast and stores the evaluator's free-text reason with a timestamp
- [ ] #2 An evaluator runs after every turn and answers only whether there is a reason to leave green, and which
- [ ] #3 [TASK-299 AC3] All five send paths read it before sending: the webhook reply path, catch-up, follow-ups, the broadcast runner, and the media acknowledgement
- [ ] #4 [TASK-299 AC4] mute is the only status that sends nothing; every other status still produces a reply
- [ ] #5 [TASK-299 AC5] pause sends a short holding answer that confirms receipt and promises no human
- [ ] #6 [TASK-299 AC6] red keeps answering normally and additionally raises the thread for urgent manual close in the operator's own surface
- [ ] #7 [TASK-299 AC7] skip answers without engaging the dodged topic and still carries the card's open question
- [ ] #8 [TASK-299 AC8] One test per status per send path proves what is sent and what is suppressed
- [ ] #9 [TASK-299 AC9] An inbound STOP/opt-out sets the candidate's status to mute through the same status column, and campaign sends read it
- [ ] #10 [Ivan 2026-10-07] The card has an ordered temperature moved by two triggers: silence over time and how lost we are; yellow is a warning, red is yellow at high temperature
- [ ] #11 [Ivan 2026-10-07] A concrete manager call is possible only after a warning or a direct request to talk to a human, and the manager phone comes only from a tool gated on that card level
- [ ] #12 [Ivan 2026-10-07] Do-not-write-again is a separate class from red and stops all sends; no candidate-facing text promises a human
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Ivan 2026-10-07 (voice, partly garbled; items marked (claude) are my reading, confirm):
- Temperature: a card moves along a temperature, ordered, by two abstract triggers: (1) silence in the chat over time, whoever owes the next step; (2) how lost we are about what to answer. This revises "statuses have no order" above; the free-text evaluator reason stays as the source of the reasons.
- Yellow = a WARNING that a human should look (a handoff with a temperature). Red = yellow at high temperature (claude). "Do not write again" is a separate class from red, i.e. mute.
- A concrete call to the manager is allowed only rarely: for a candidate who already got the warning, or who asks directly how to talk to a human.
- The manager phone number is revealed only by a tool gated on the card level (the model cannot name it before). No human status and no instruction on how to answer: we wait and the priority rises gradually.
- The candidate-facing text never promises that a human will call or answer (TASK-314 AC 15). Handoff and temperature are internal.
- Needs from Ivan: the manager number, who answers, hours. Config only, never in the repo.
- Already received documents are never asked again: the card states reach every system that shapes a reply (brain, closing gate, follow-up picker).
- The old yellow flag (TASK-314 decision 7: missing data) becomes one reason within the scale, not a separate meaning (claude).
<!-- SECTION:NOTES:END -->
