---
id: TASK-345.11.2
title: >-
  Mailer forwards every clinic answer, bounce and stop to both operators as it
  arrives, without a Claude session
status: Done
assignee:
  - '@claude'
created_date: '2026-10-05 09:14'
updated_date: '2026-10-05 11:43'
labels: []
dependencies: []
parent_task_id: TASK-345.11
priority: high
ordinal: 289000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05: "у нас должна быть автоотправка емейла на наши с Валентином емейлы". A clinic answer (Ilmtalklinik Pfaffenhofen, Karin Nadler, "schicken Sie mir bitte Ihre Konditionen", Fri 02.10 11:45) sat unseen for three days: the batch logged it as an inbound event and took the clinic out of its sequence, but nothing mailed the operators, and forwarding was a manual step (tools/daria_forward.py, then to Ivan only) done only inside a Claude session. Nobody worked on Friday afternoon or over the weekend. The Rotkreuz bounce the same day only reached the operators as a halt notice without the report. With bounce removed from halt_on on 02.10 (a spam rejection by one clinic server stopped the whole wave), a bounce no longer produces any mail at all.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Every new inbound event the watch logs with kind reply, stop, bounce, complaint or unmatched is mailed to every address in the campaign "operators" from the sender box, naming the clinic, the sender and the kind in Russian, with the original attached as message/rfc822
- [x] #2 auto_reply (out-of-office) is not forwarded; it stays in the ledger only
- [x] #3 A failed forward fails loudly: the batch halts with the error and the message is not marked seen, so the resumed batch forwards it
- [x] #4 tools/daria_forward.py (manual) sends to every operator in its TO list (Ivan only while Valentyn has no working address, Ivan 2026-10-05)
- [x] #5 tests/test_clinic_mailer.py covers: a reply is forwarded once to every operator with the original attached, an auto_reply is not, a failing SMTP send leaves the message unlogged, an undeliverable operator mail is no clinic bounce, two watches of one ledger run one after the other
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. In tools/clinic_mailer.py add forward_inbound(cfg, box, raw, folder, kind, clinic): one mail from the sender box to cfg operators, subject with the Russian kind label and clinic, text with From/To/Subject/date and the message text, original attached as message/rfc822 (same layout as tools/daria_forward.py notification). 2. In watch(): for kind in reply/stop/bounce/complaint/unmatched, call it BEFORE append_ledger, so a failed SMTP send raises MailerError (batch halts as error, resumable) and the message stays unseen and is forwarded on resume. 3. Test in tests/test_clinic_mailer.py with the existing fakes. 4. Restart wave 1 (pid 550017) so it runs the new code and the halt_on without bounce; wave 2 batches launch with the new code.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
05.10: forward_inbound in tools/clinic_mailer.py (watch mails reply/stop/bounce/complaint/unmatched to the campaign operators before the ledger line; failure leaves the message unseen). New kind operator_undeliverable: a bounce naming an operator address is not a clinic bounce and goes only to the other operators (Valentyn's address v.vihandt@ndt-group.agency returned 'Recipient Unknown' on 05.10; the NDR of a forwarded clinic answer quoted our message-id, so wave 1 halted on it and, once forwarded, it would loop). tools/daria_forward.py sends to both operators. tests/test_clinic_mailer.py: 3 new tests, 49 pass with test_daria_desk.py. Not yet seen live: a real clinic answer forwarded by a running batch.

05.10 evening: live check: LMU Klinikum München answered at 11:30:48 (ledger inbound, recipient 16290) and the answer was mailed to the operators by the running batch (forwarded_to and forward_message_id in the ledger line). Valentyn's address bounced, so operators in campaign.json, campaign.w2.json, data/email-analysis/desk/daria.json and TO in tools/daria_forward.py are Ivan only until Valentyn gives a working address. Batches of one campaign run as separate processes on one ledger (wave 2 first letters and its follow-up batch): watch() now takes an exclusive flock on <ledger>.watch.lock so two processes never forward the same answer twice. Tests: .venv/bin/python -m pytest tests/test_clinic_mailer.py tests/test_daria_desk.py, 52 passed.

05.10 late, Ivan: roles. Valentyn's new address is ukraine.bz1@gmail.com. Config of each campaign: "operators" = who may command (Ivan, Valentyn), "notify" = announcement, reports, halt, resumed, done (Ivan only), "forward" = per inbound kind who gets the answer (reply and unmatched: Ivan and Valentyn; stop, bounce, complaint, operator_undeliverable: Ivan). The desk config has "notify" for its stop notice. A batch planned earlier keeps its old recipients in the batch file; notices are cut to the notify list. The pre-announcement wait of a scheduled batch now reads the inbox too, so wave 1 answers are forwarded before the Friday announcement. Nadler and LMU answers re-sent to Valentyn with tools/daria_forward.py send ... --to ukraine.bz1@gmail.com.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Clinic answers (reply, stop, bounce, complaint, unmatched) are mailed to every operator from the sender box as they arrive, original attached; auto replies stay in the ledger; a failed forward halts the batch and leaves the answer unseen; an operator address in a bounce report is its own kind and never halts a wave. Verified by 52 passing tests and live: the LMU answer of 05.10 11:30 was forwarded by the running batch.
<!-- SECTION:FINAL_SUMMARY:END -->
