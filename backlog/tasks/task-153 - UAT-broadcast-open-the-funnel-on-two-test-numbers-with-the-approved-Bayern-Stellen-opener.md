---
id: TASK-153
title: >-
  UAT broadcast: open the funnel on two test numbers with the approved
  Bayern-Stellen opener
status: To Do
assignee: []
created_date: '2026-09-22 07:34'
updated_date: '2026-09-22 07:35'
labels:
  - wa-transport
dependencies:
  - TASK-121
  - TASK-131
ordinal: 161000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Run the acceptance test as a real campaign on the phone rail: send the opener to Ivan and his partner only, then let Luna carry whoever answers yes through qualification from scratch.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The opener goes out on the phone rail as plain text carrying the approved wording of recruitment_bayern_stellen_interesse_de, with the two choices rendered as numbered lines, and no board link
- [ ] #2 Recipients are exactly two numbers: Ivan and his partner. The third test number is excluded, and the run refuses to start if any number outside that pair is in the list
- [ ] #3 A typed agreement ('ja', '1', 'Ja, ich habe Interesse') opens the funnel: the thread is answered by Luna and qualification starts from the first gate
- [ ] #4 No history and no card are fabricated for a test recipient: the thread starts empty and is treated as a first contact, even where the old number holds a history
- [ ] #5 A typed refusal ('nein', '2') is recorded as a decline and no follow-up is sent
- [ ] #6 The run is idempotent: re-running the same campaign id sends nothing a second time, and a crash mid-run resumes without a duplicate
- [ ] #7 Every send is verified by its own delivery tick, and the run reports per recipient what was sent and what came back
<!-- AC:END -->
