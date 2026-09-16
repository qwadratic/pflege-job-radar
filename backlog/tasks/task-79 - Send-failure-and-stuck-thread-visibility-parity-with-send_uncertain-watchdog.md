---
id: TASK-79
title: >-
  Send-failure and stuck-thread visibility (parity with send_uncertain +
  watchdog)
status: In Progress
assignee: []
created_date: '2026-09-13 11:14'
updated_date: '2026-09-13 11:25'
labels: []
dependencies: []
ordinal: 79000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Recon found a Meta send failure in our harness just raises and the route answers 502 with nothing durably recorded -- module docstring says the turn 'is not recorded as sent' but today nothing is recorded about the failure either, so there is no trace at all beyond an HTTP error in a log somewhere. The real system marks a distinct send_uncertain state and escalates via a de-duplicated owner notification, picked up by a watchdog. We have no notification channel (no Telegram/email integration exists in this repo) so do not invent one -- build the honest, available equivalent: a durable failure record and a discoverable stuck-thread flag.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A Meta send exception is caught long enough to persist a durable failure record (thread and/or a dedicated table) before being re-raised (the loud-failure/502 behavior for the caller is unchanged)
- [x] #2 A thread whose ball has been on us longer than WA_STUCK_REPLY_HOURS (env var, sane default) is flagged as stuck reply owed
- [x] #3 Both the last-send-error state and the stuck-reply flag are readable via an existing or new owner-gated endpoint -- no invented notification channel (no email/Telegram) since none exists in this repo
- [x] #4 Unit tests cover: a send failure is durably recorded, a stuck thread is flagged, a healthy thread is not flagged
- [x] #5 Full offline suite stays green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
app/wa/store.py: wa_send_failures(phone, error, at) + record_send_failure/recent_send_failure. app/wa/api.py: _send_and_record() wraps _send(), records the failure then re-raises (the loud-502 behavior is unchanged). GET /wa/threads now computes stuck_reply per row (_is_stuck: ball_for()=='us' and older than C.STUCK_REPLY_HOURS, default 2h) and includes last_send_error when one exists. No notification channel invented (none exists in this repo) -- durable + discoverable only, per CLAUDE.md's no-invented-safety-nets rule read the other way (don't invent a channel either).
<!-- SECTION:NOTES:END -->
