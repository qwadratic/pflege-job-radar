---
id: TASK-396
title: >-
  Handoff lane: Daria (email harness) closes consented WA leads and reports
  leads to Ivan and the parallel operator
status: To Do
assignee: []
created_date: '2026-09-29 23:08'
updated_date: '2026-10-06 12:51'
labels:
  - pro-api
  - email-lane
dependencies:
  - TASK-395
  - TASK-345
priority: high
type: feature
project: whatsapp
ordinal: 271000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
wa_queue_candidates.status is only ever "queued". The Pro Leads view therefore counts every consented lead as needing a human, forever. pflege-fe raised this on 2026-09-29.

**Ivan, 2026-09-30:** link the two lanes. Daria, the digital employee in the email harness (daria.s@pflege-connect.work, TASK-345), takes over consented WA leads and prepares lead reports for Ivan and the parallel operator.

Shape, to agree with the email-harness session:
- **Daria reads** consented leads and their matched clinics through the WA harness token API (TASK-395 surface plus the TASK-326 queue).
- **Daria writes back** a handoff status after acting: sent to clinic, clinic answered, closed or declined. This goes through one token-gated write endpoint. It is the first write on the harness API, and Ivan approved it with this link.
- **The Pro API rows** (handoff.status) show that status, so a closed lead leaves "needs a human".
- **Daria emails lead reports** to Ivan and the parallel operator, built from the same API. Cadence and format are agreed with Ivan.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The interface with the email-harness session is agreed and written into this task: what Daria reads, the write endpoint, the status values
- [ ] #2 A token-gated write endpoint records a handoff status per queue row, with an audit trail (who, when, previous status); tests included
- [ ] #3 Pro API rows expose the handoff status, and a closed handoff no longer counts as needing a human
- [ ] #4 Daria sends Ivan and the parallel operator a lead report built from the API; the first one is reviewed by Ivan
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-30, the email-harness session's spec for Daria (full reply, summarised):

**READ, per consented lead, keyed by candidate_id; the phone stays masked:**
- structured CV JSON (roles with from/to, employer, ward, tasks, languages, Anerkennung, city);
- CV/Urkunde metadata (present, verified, date); the files themselves are never needed;
- CONSENT: verbatim text, timestamp, channel, message id, scope (one named clinic vs generic). This is a must, because nurse-79's recorded consent named one clinic only;
- named towns, all of them, verbatim with message ids;
- housing, ward wish, qualification path, German level plus proof, household size;
- IN-PROCESS clinics: submissions, interviews, trial days, offers, manager-takeover flags with reason (a must);
- candidate status: placed / signed / withdrawn / unreachable, with ts;
- current and past employers (hard exclusion);
- updated_at on every row and a source-freshness stamp per response;
- message history with ids.

**WRITE-BACK, keyed by (candidate_id, clinic)**, since one lead fans out to 10-20 clinics:
- fields: board clinic_id + name, status, sender box, Message-ID, batch_id, note, ts; audit trail kept;
- statuses: sent_to_clinic | followup_sent | clinic_replied | interview_scheduled | trial_scheduled | offer | contract_signed | declined | closed | halted;
- contract_signed is the goal state;
- needs-a-human: sent_to_clinic/followup_sent clear it; clinic_replied, interview_scheduled, trial_scheduled, offer and halted raise it again.

**REPORTS:** a Russian plain-text mail from daria.s@ via tools/clinic_mailer.py to the parallel operator, Cc Ivan (or Ivan alone), with the nurse-79 sections.
- Cadence is ad hoc for now. Ivan prefers an evening summary but has not picked a trigger: no timer or cron until he does.
- Fields per lead: candidate_id, qualification + Anerkennung, wards, towns, housing, consent scope/date, in-process clinics with stage, planned clinics, latest clinic replies, open questions.

**SPLIT:**
- consent, card, CV, documents and messages come from wa.sqlite;
- in-process clinics, placement status and candidate_id live in sales_brain (the colleague's CRM, read-only for us) plus Daria's send log. The join design is still to decide.
<!-- SECTION:NOTES:END -->
