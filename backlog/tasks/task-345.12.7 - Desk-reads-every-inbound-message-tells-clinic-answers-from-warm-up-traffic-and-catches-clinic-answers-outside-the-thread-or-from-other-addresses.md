---
id: TASK-345.12.7
title: >-
  Desk reads every inbound message: tells clinic answers from warm-up traffic
  and catches clinic answers outside the thread or from other addresses
status: To Do
assignee: []
created_date: '2026-10-02 07:00'
labels:
  - email
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 287000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-02: "это прогрев. классификатор должен отличать сообщения от клиник от прогревных. ответы от клиник могут приходить вне тредов и с других ящиков, нужно внимательно перепроверить входящие". daria.s@pflege-connect.work gets dozens of warm-up messages a day (and answers some of them itself). The batch watch in tools/clinic_mailer.py matches a clinic answer only by thread, a quoted Message-ID or the exact address it wrote to; a message from another address of the same domain is logged as "unmatched" and only printed, and a message from any other domain is dropped without a trace. So a clinic that answers from a colleague's or a private address, or in a new mail, keeps getting follow-ups and nobody is told. A running batch reads its ledger on every poll but loads its code once, so the fix belongs in the desk, which can be restarted without touching the batches.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every inbound message to the sender box that no batch matched by thread, quoted Message-ID or address is classified as warm-up/other or as a clinic message, and the verdict is logged
- [ ] #2 A clinic message is written into the right campaign ledger as an inbound event for that clinic, so its follow-ups stop and halt_on kinds halt the batch; the operators get one mail naming the clinic, the sender and why it matched
- [ ] #3 A message that looks like a clinic's but matches no clinic of any campaign is mailed to the operators; it is never dropped
- [ ] #4 A classifier failure stops the desk loudly (desk_stopped and a mail), and the desk heartbeat stays fresh while it classifies
- [ ] #5 A one-time pass over every inbound message since the first letter of nurse-79 wave 1 (29.09.2026) is run and its verdicts are reported to Ivan
<!-- AC:END -->
