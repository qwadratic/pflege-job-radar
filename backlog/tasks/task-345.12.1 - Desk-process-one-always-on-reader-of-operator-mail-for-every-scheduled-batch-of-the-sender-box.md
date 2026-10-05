---
id: TASK-345.12.1
title: >-
  Desk process: one always-on reader of operator mail for every scheduled batch
  of the sender box
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-01 18:10'
updated_date: '2026-10-05 17:04'
labels:
  - email
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 279000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two scheduled batches (nurse79 wave 1 until 09.10, nurse79-2 from 02.10) share daria.s@pflege-connect.work and the same two operators. Each batch process today reads every operator mail since its own announcement and acts on it, so a "стоп" meant for one wave halts both and every mail is answered twice. Outside a running batch nothing reads operator mail at all.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One desk process reads every operator mail of the sender box, running or not, and answers each exactly once to all operators
- [ ] #2 stop, skip and status act on the batch the mail names or answers (thread), and on every running batch when it names none; skip acts on the batch that holds the named clinics
- [ ] #3 A batch whose config says "desk": true does not read operator mail itself; it halts loudly (ledger and mail to the operators) when the desk heartbeat is older than the configured limit
- [ ] #4 A desk error is mailed to the operators and logged before the desk exits
- [ ] #5 Unit tests cover routing a mail to its batch, the single answer and the heartbeat halt
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. clinic_mailer desk mode: config desk {heartbeat, max_age_seconds, poll_seconds}; check_desk() instead of handle_commands in poll_until and the start-up self-check; a stale or missing heartbeat raises MailerError, so the batch halts with kind error (resumable) and mails the operators.
2. tools/daria_desk.py run: every poll_seconds stamp the heartbeat, read operator mail via daria-inbox since the last poll minus overlap, dedupe against the desk ledger and the campaigns' command events.
3. Route by thread: In-Reply-To/References against each batch's announced/report/notice/command Message-IDs and the desk's own answers; no match = every active batch. Classify once against all batches in scope (intent, batch_ids, recipient_ids); stop/status act on the named batches (none named = all in scope), skip on the batch holding each clinic; one answer to all operators.
4. Questions and other_command go to a worker thread (TASK-345.12.2); unanswered ones are re-queued on restart.
5. Any desk error: desk_stopped in the ledger, a mail to the operators, exit.
6. Tests in tests/test_daria_desk.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-01: implemented tools/daria_desk.py (run/ask/route), desk mode in tools/clinic_mailer.py (check_desk, poll_until, send_scheduled, commands_help) and desk texts in tools/mailer_announce.py. Desk config data/email-analysis/desk/daria.json (gitignored) lists nurse79 wave 1 and nurse79-2; both campaign configs now carry desk {heartbeat ../../../desk/heartbeat.json, max_age_seconds 600, poll_seconds 60}; wave 2 announces with w2/announce.txt (stop in this thread stops only wave 2). tests/test_daria_desk.py: 11 passed. Not deployed: the desk runs as root (campaign ledgers are root-owned), so Ivan starts it; the running wave-1 process (pid 3494830, old code) must be stopped and restarted after the desk is up, or it keeps answering operator mail itself.

Ivan, 2026-10-01: the 10-minute heartbeat limit (desk.max_age_seconds 600) is confirmed ("да").

Ivan 2026-10-05 evening: the desk crashed at 16:59 on a daria-inbox HTTP 404 (a message vanished between the list and the fetch). Now the read of the operators' mail is retried three times (10 s apart); if it still fails the desk logs read_error and mails the notify list once per outage, and goes on with the halt notices, the digest and the answer and redirect threads. It stamps no heartbeat and does not move its read position until a read succeeds (a stop by mail would go unread, so the batches halt themselves after their limit; the mail that arrived meanwhile is read afterwards). Also: the redirect letters (TASK-345.12.9) are sent from a desk thread; a stop by mail does not stop them (Ivan's decision). tools/daria_desk.py poll/read_operator_mail, tests/test_daria_desk.py.
<!-- SECTION:NOTES:END -->
