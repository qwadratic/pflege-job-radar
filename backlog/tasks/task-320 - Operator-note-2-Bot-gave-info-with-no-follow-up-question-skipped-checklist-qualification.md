---
id: TASK-320
title: >-
  Operator note #2: Bot gave info with no follow-up question; skipped checklist
  qualification
status: Done
assignee: []
created_date: '2026-09-29 10:00'
updated_date: '2026-09-30 18:28'
labels:
  - operator-note
dependencies: []
priority: medium
project: whatsapp
ordinal: 265000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Origin
- Operator note #2, test thread +436…6780
- Received: 2026-09-29T09:55:04+00:00 UTC / 2026-09-29 11:55 CEST
- Transcribed voice note
- Note wamid: wab.i.autolink.c6e5c570c6e891ef9c39dbba51f6b4bf

## Verbatim (Russian)
> Так, смотри, значит, ты написал мне информацию и не задал никакого вопроса, коммуникация должна идти, во-первых, по нашему чек-листу, то есть сначала нужно квалифицировать, или это медсестра уже с подтвержденным дипломом, потом понять, что она хочет, ну, то есть как-то надо обработать этот чек-лист, можешь мне вообще скинуть его, чтобы я на него посмотрел изначально, как он у нас выглядит, возможно, у него есть какие-то корректировки, но тут вне зависимости от чек-листа ты просто дал информацию и за этим текстом не стоит никакой следующий шаг, вот, соответственно, это некорректно, прими этот комментарий, обнови систему и удали мою переписку и давай начнем заново.

## Decoded request
Operator reports that the bot replied to the inbound message with information but did not ask any question in return. Communication must follow the team's checklist: first qualify whether the person is already a nurse with a confirmed diploma, then determine what she wants. The operator asks to be sent the current checklist so they can review it and possibly suggest corrections. Regardless of the checklist, the operator says the bot's reply gave information without any next step, which is incorrect. The operator asks the team to accept this feedback, update the system accordingly, delete this test conversation, and restart it.

## Open questions
- What exactly should be 'updated in the system' -- the bot's prompt/logic, or something else specific?
- Should 'delete the conversation and start again' mean clearing this test thread's history/state only, or something broader?
- Is there an existing checklist document, or does one need to be created before it can be sent to the operator?

## Database context
### Recent messages (+436…6780)
- [2026-09-29T09:45:27+00:00] in text: Hallo, suche eine Stelle
- [2026-09-29T09:52:42+00:00] out text: Hallo! Schön, dass Sie sich melden. 😊 Ich bin Valentina von der NDT Group – aktuell haben wir 2.878 offene Pflegestellen an bayerischen Kliniken.
- [2026-09-29T09:54:18+00:00] in text: 🎤 Sprachnachricht (0:53)
- [2026-09-29T09:54:45+00:00] in audio: Так, смотри, значит, ты написал мне информацию и не задал никакого вопроса, коммуникация должна идти, во-первых, по нашему чек-листу, то есть сначала нужно квалифицировать, или это медсестра уже с подтвержденным дипломом, потом понять, что она хочет, ну, то есть как-то надо обработать этот чек-лист…

### Thread card
```json
{"asked": [], "is_test": true, "last_inbound_at": "2026-09-29T09:54:45+00:00", "last_outbound_at": "2026-09-29T09:55:53+00:00", "matches_sent_at": null, "opened_at": "2026-09-21T12:35:39+00:00", "phone": "+436…6780", "rail": "bridge", "slots": {}, "stopped": false, "stopped_reason": null, "test_marked_at": "2026-09-22T20:33:54+00:00", "turns": 3}
```

### Earlier notes from this phone
(no earlier notes from this phone)
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Bot responses always end with a next step or question rather than just information
- [x] #2 Bot follows the checklist: first qualifies whether the contact is a nurse with a confirmed diploma, then determines what she wants
- [x] #3 Operator is sent the current version of the checklist for review
- [x] #4 The test conversation on this thread is deleted and restarted
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-29 10:05 UTC (wa-harness): root cause of 'no question' was not the prompt. Bridge answered 503 (phone lock busy inside the bridge process since 08:36) mid-turn; bubble 1 went out, bubble 2 ('Suchen Sie eine Stelle in Bayern?') never did, and every catch-up re-drive then failed recording the replayed bubble 1 (UNIQUE wa_messages.wamid). Fixed: api._send skips re-recording a replayed wamid (store.outbound_recorded) + test; bridge restarted on the mini. Thread wiped (purge_test_history). Checklist sent in the completion note; asked Valentyn whether qualification should move before region. AC#1 already a prompt rule (rule 8) + closing gate; AC#2 waits for his answer.

Valentyn (note #3): checklist order stays as is. Closed.
<!-- SECTION:NOTES:END -->
