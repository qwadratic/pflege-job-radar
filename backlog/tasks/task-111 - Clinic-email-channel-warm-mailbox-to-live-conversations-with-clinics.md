---
id: TASK-111
title: 'Clinic email channel: warm mailbox to live conversations with clinics'
status: To Do
assignee: []
created_date: '2026-09-17 17:31'
updated_date: '2026-09-28 13:14'
labels:
  - email
dependencies: []
documentation:
  - backlog/docs/email/doc-1 - Email-channel-runbook.md
priority: high
ordinal: 111000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
New channel of the product: email communication with clinics (active threads, meeting scheduling, follow-ups), not a one-shot mass campaign. Business use starts ~2026-09-24 when traffic flows; this week builds and tests the infra. Ivan works through the subtasks one at a time, manually, no autopilot. Every step must leave notes and pitfalls in the runbook doc so the setup can be repeated in other projects.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 All subtasks Done
- [ ] #2 Runbook doc reflects every step taken and every pitfall hit
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-28, Valentyn's reply to Daria's nurse-79 report (10:47): (1) no letter to clinic 4, Klinikum Kempten, and put it on the do-not-contact list; (2) the subject must be short and concrete, one short sentence at most, not every matching Bereich; the rest is fine, send after that. Done: our own do-not-contact list data/email-analysis/do_not_contact.json (gitignored; entry: domain klinikverbund-allgaeu.de), read by clinic_mailer (config key do_not_contact; plan skips a blocked To and drops a blocked Cc) and by mailer_recipients.py (no letter); campaign.json names it. Subject: new var BETREFF, one Bereich per clinic ('Pflegekraft für Ihre Intensivstation' etc.), used by all three templates. recipients.json now has 9 clinics. Test added (tests/test_clinic_mailer.py, 11 pass). Launch still waits for Ivan's verbatim approval of the German texts and subjects; Valentyn's go is not Ivan's approval.
<!-- SECTION:NOTES:END -->
