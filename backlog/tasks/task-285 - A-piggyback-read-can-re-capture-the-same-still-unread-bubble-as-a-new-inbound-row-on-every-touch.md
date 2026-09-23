---
id: TASK-285
title: >-
  A piggyback read can re-capture the same still-unread bubble as a new inbound
  row on every touch
status: To Do
assignee: []
created_date: '2026-09-23 19:44'
labels: []
dependencies:
  - TASK-235
priority: high
project: whatsapp
ordinal: 238000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found live during the 2026-09-23 UAT, on Ivans own test thread (+436704048778). The SAME
physical WhatsApp bubble -- "Ich habe Interesse an einer Stelle in München", never answered because
of a same-day Supabase outage and a separately-broken catchup unit -- was captured into
wa_messages as three DISTINCT inbound rows:

  06:29:43  first capture (an earlier watcher or piggyback read)
  08:22:47  second capture (another touch of the chat)
  19:27:33  third capture, during tonights broadcast send

Same bubble, same text, never marked read on the handset in between (the candidate never sent
anything new), yet three separate rows exist. bridge/inbound.py mints an inbound id from
counterparty + local date + HH:MM + normalised text + occurrence index -- the occurrence index is
exactly what lets the SAME text on the SAME day be captured again as a "new" occurrence every time
a different minute reads it, because nothing here checks whether the bubble was already captured
under an earlier minute stamp with the same content and no intervening outbound.

Consequence, observed directly: this makes an old, already-seen, still-unanswered message look like
it needs answering again on every touch of the chat -- api.process_owed_turn (or catchup) would
process it as owed each time, which is likely a real contributor to TASK-235/238s "catch-up burns a
brain call every three minutes forever" pattern, not a separate root cause. It also means a
threads last_inbound_at keeps moving forward even though the candidate said nothing new, which
corrupts anything that reads that field as "when did they last actually speak" (SLA timers, the
fresh-context framing this UAT needed).

Distinct from TASK-284 (which is about the OUTBOUND side losing memory of a broadcast): this is the
INBOUND side re-discovering the same unread content repeatedly.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The same still-unread bubble, read on two different occasions with no new outbound or new candidate text in between, produces at most one inbound row
- [ ] #2 A test reproduces the exact pattern: capture a bubble, capture it again from a different minute stamp with identical text and no intervening send, assert only one row exists
- [ ] #3 last_inbound_at on wa_threads does not move forward on a re-capture of the same already-seen content
- [ ] #4 A genuinely new message with the same text as an old one (the candidate repeats themselves) is still captured -- the fix keys on "already captured, unanswered", not on text alone
<!-- AC:END -->
