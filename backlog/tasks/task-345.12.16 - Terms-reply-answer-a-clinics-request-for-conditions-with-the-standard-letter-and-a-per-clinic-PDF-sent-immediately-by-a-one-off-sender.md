---
id: TASK-345.12.16
title: >-
  Terms reply: answer a clinic's request for conditions with the standard letter
  and a per-clinic PDF, sent immediately by a one-off sender
status: To Do
assignee: []
created_date: '2026-10-07 09:16'
labels:
  - mailer
  - daria
  - nurse-79
  - classifier
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 325000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When a clinic answers a mailing and asks for conditions or price ("Konditionen", "Kosten", "Preis", "Honorar"; the classifier already labels this pattern terms_request, TASK-345.12.9), Daria answers in the same thread (reply to the clinic's message, subject "AW: <their subject>") with a short template letter and a PDF "NDT_Konditionen_<clinic>.pdf" made for that clinic (clinic name and addressee on it). Standard terms since 2026-10-06: 4,990 EUR net per hired nurse, 50 % at signing of the employment contract, 50 % after the third month, no refund; the e-mail confirmation of the clinic is the order. The full profile with the name goes out only after the clinic's written yes (not part of this task). The old placement contract is no longer used for new answers. Operator decision (Ivan, 2026-10-07): the first such letter is prepared and sent by hand inside a session, then fixed as the template; after that the classifier may send it by itself, without a plan, an announcement or an approval per letter.

Needed: (1) an "immediate" mode of the mailer: no plan, no announcement, no send window or odd-minute wait, one-off sender that sends and exits; it keeps the checks that guard recipients (suppression and do-not-contact lists, bounce/stop handling) and records the letter in the ledger as its own event kind, not as a cadence step (a "sent" event with a step outside the cadence breaks next_due and step_name); (2) the letter template and an HTML template for the PDF rendered with the repo's Playwright (.venv only); (3) the thread headers: In-Reply-To = the clinic's message, References = the whole chain; (4) the classifier path: pattern terms_request triggers the letter once per clinic and thread, tells both operators in the digest, and does not trigger again after a reply; (5) tests on the mirror/fixtures, never a live site.

Requested by the mailer lane (pflege-board-25) through pflege-clawl, 2026-10-07. No person or clinic names in this task (public repo).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A terms_request answer in a fixture produces exactly one immediate letter with the PDF attached and correct thread headers
- [ ] #2 A second terms_request in the same thread produces no letter
- [ ] #3 A suppressed address produces no letter and a loud record
- [ ] #4 No letter has a link
- [ ] #5 The ledger event is read by watch and the digest without errors
<!-- AC:END -->
