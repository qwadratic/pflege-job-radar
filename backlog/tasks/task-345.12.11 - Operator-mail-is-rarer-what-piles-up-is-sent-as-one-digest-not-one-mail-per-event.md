---
id: TASK-345.12.11
title: >-
  Clinic answers reach the operators once a day as one HTML digest, not one mail
  per answer
status: Done
assignee: []
created_date: '2026-10-05 11:42'
updated_date: '2026-10-06 12:50'
labels: []
dependencies: []
parent_task_id: TASK-345.12
priority: high
ordinal: 293000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan, 2026-10-05: Daria writes to the operators too often; what accumulates may accumulate. Roles set the same day: Ivan gets every notice (announcement, round report, halt, resumed, done, desk stopped, errors) and every clinic answer; the parallel operator gets no mailing notices and only the clinic answers nobody has handled yet (kinds reply and unmatched, config "forward" of each campaign), his commands and questions are still read and answered by the desk. Today each clinic answer is forwarded the minute it arrives (TASK-345.11.2, done) and every notice goes out alone, so a restart or a bad hour can mean several mails in a row.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The watch no longer mails a clinic answer; it keeps the original next to the ledger and marks the inbound event with the addresses of the campaign "forward" list for its kind (digest_to)
- [x] #2 The desk mails each address one digest a day from "digest_at" (17:00 Berlin, desk config) with every answer no digest carried yet: an HTML table (received, clinic, campaign, kind, sender, subject and text), the same rows in the text part, every original attached as message/rfc822; no answers, no mail; a day with a digest logged is done
- [x] #3 Each digest is written to the ledger of every campaign it carried (event digested, address, message ids) and to the desk ledger; a failed digest send raises, the desk stops loudly, and the next digest carries the same answers
- [x] #4 Halt notices are not part of the digest (see TASK-345.12.14); the announcement and round reports go out at their planned times
- [x] #5 tests cover: answers are kept and mailed once per address with table and originals, a failed send keeps them for the next digest, the desk sends once a day from digest_at, the per-kind forward lists
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
05.10: done in tools/clinic_mailer.py (spool_inbound, digest_mail, digest), tools/daria_desk.py (digest_due, run loop), desk config digest_at 17:00 (Ivan: once a day; the hour is my choice). Tests: 56 passed (tests/test_clinic_mailer.py, tests/test_daria_desk.py). Not yet seen live: the first digest goes out at 17:00 Berlin on a day with new answers. Immediate forwards of 05.10 stay in the ledger as forwarded_to; only answers logged after the deploy carry digest_to.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Clinic answers are kept by the watch and mailed by the desk once a day (17:00 Berlin) as one HTML digest per address with a table and the originals attached; Ivan gets every kind, the parallel operator replies and unmatched. Verified by tests; first live digest pending.
<!-- SECTION:FINAL_SUMMARY:END -->
