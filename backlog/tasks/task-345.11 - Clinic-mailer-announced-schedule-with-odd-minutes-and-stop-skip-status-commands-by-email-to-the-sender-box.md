---
id: TASK-345.11
title: >-
  Clinic mailer: announced schedule with odd minutes, and stop/skip/status
  commands by email to the sender box
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-28 14:39'
updated_date: '2026-09-28 16:20'
labels:
  - email
dependencies: []
parent_task_id: TASK-345
priority: high
ordinal: 264000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-09-28, for the nurse-79 launch (10 clinic letters from daria.s@pflege-connect.work): no fixed pause between a first letter and the rest; instead the whole plan is announced at 09:00 sharp to Ivan and Valentyn in one mail with one PDF (schedule to the minute, the common letter and follow-up templates, the cadence rules, every anonymised Kurzprofil version side by side on one page, one example letter with signature on one page), then one hour passes before the first letter. Every send time is odd (never a round minute), with random pauses. At any time after the announcement Ivan or Valentyn can stop or cancel the mailing by writing to daria; a small Haiku classifier decides whether their mail is a stop/cancel or another mailing command, the process executes what it can and answers both of them with the result, or says it cannot and an operator is needed. Clinic letters still need Ivan's approval of the exact batch and his own live run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 plan --announce-at ISO writes a batch whose every clinic letter has a send_at: the first at least the configured window after the announcement, gaps drawn from pause_seconds, no minute divisible by 5, all inside the send window; the approval covers send_at, the announcement and the operators
- [ ] #2 send waits for the announcement time, sends the announcement with its PDF to the operators, then sends each letter at its send_at, and refuses to start when the announcement would leave less than the window before the first letter
- [ ] #3 A mail from an operator address received after the announcement is classified (claude -p, Haiku) as stop, skip, status, other command or not a command; stop halts or cancels the batch, skip drops named clinics that are still pending, and every operator mail gets an answer to all operators with the result or with 'not available, operator needed'
- [ ] #4 A classifier failure, an automatic halt (bounce, complaint, stop reply) or any error after the announcement is reported to the operators by mail and in the ledger
- [ ] #5 Unit tests cover schedule, approval, command handling and failure paths; one end-to-end run on a compressed schedule to our own boxes shows announce, a stop by mail, the halt and the answer
- [ ] #6 One approval covers every cadence step: plan --announce-at [--start-at] plans each clinic's first letter and its follow-ups at the same time of day on their cadence days; follow-ups go by themselves in the clinic's thread (In-Reply-To and References set at send time) and only to clinics that did not answer and were not taken out
- [ ] #7 Before each follow-up round a preliminary report goes to the operators at start_at minus the window on that day (08:00 for a 09:00 start): who gets the step and when, who does not and why, one letter as an example; commands are read from the announcement to the last letter of the batch
- [ ] #8 An error halt (network, classifier, Ctrl-C, SIGTERM, SIGHUP) is resumed by the same send command while no send time has passed; an operator stop or a delivery halt needs a new plan
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. clinic_mailer: schedule (odd minutes, window after announce), announce block in batch and approval. 2. Inbox source daria-inbox for non-root runs. 3. Operator commands: fetch, classify via claude -p Haiku, act, answer; halt notices. 4. Announcement PDF renderer (tools/mailer_announce.py): plan table, templates, cadence, Kurzprofil versions page, example letter page. 5. nurse-79: tenth clinic 56403 with its own Kurzprofil marks, regenerate, plan for Tue 29.09 09:00. 6. Tests, e2e on compressed schedule to own boxes, classifier smoke test.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-28 evening, Ivan: follow-ups are automatic under the one approval (no separate approval per round); on each follow-up day a preliminary report precedes the round and commands are taken in that window; the plan may be announced right away and the start moved to 09:00 (first letter about 09:02). Implemented: plan carries every remaining cadence step per clinic (item round), reports before each follow-up round, round notices after each non-final round, done notice, resumable error halts (resumed event), SIGTERM/SIGHUP handled like Ctrl-C, classifier input carries what is left to send. Classifier smoke test 40/40.

e2e #2 (2026-09-28 17:57-18:18, batch nurse79-e2e-20260928-1752, compressed cadence fu1/fu2 after 10 min, own boxes): announcement with PDF, status command, 3 first letters, a clinic reply stopped that clinic's chain, round notice, fu1 report (2 of 3), skip command, fu1 in thread (In-Reply-To = first letter), fu1 round notice, fu2 report (1 of 3), stop command halted the batch and was answered; exit 0.

Fixes found by e2e #2: email.policy.default folding split a Cyrillic subject into encoded words right at a space and readers dropped it ('первыеписьма'); MailPolicy now encodes a non-ASCII Subject with email.header.Header (verified live: Exchange parses the space); wording: 'фоллоу-ап 1 ушёл', a one-letter report says 'в 18:23' instead of a span, the round notice gives the report time then the letters, a stop answer omits the command list. Unit tests 33/33.

Real batch planned: nurse79-20260928-1809 (announce 2026-09-28 18:25 or when Ivan starts the send; first letters Tue 29.09 09:02-09:23; fu1 Fri 02.10, report 08:00; fu2 Fri 09.10, report 08:00). Awaiting Ivan's approval and his live run.
<!-- SECTION:NOTES:END -->
