---
id: TASK-456
title: >-
  Pay in the posting markup: tariff tag, grade and the printed figure as numbers
  with unit and source, also from the clinic default
status: To Do
assignee: []
created_date: '2026-10-08 12:07'
labels:
  - matching
  - data-quality
dependencies:
  - TASK-108
priority: high
ordinal: 338000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-10-08: TVoeD and similar markers/tags that point to the expected pay, and a direct mention of pay in the posting, must be in the markup. Today (measured 2026-10-08 on 3498 open postings): an enr_tariff tag on 1329 (38.0 percent: TVoeD 776, AVR unspecified 274, TV-L 130, AVR Caritas 83, AVR Diakonie 36, Haustarif 30), a pay grade on 429 (12.3 percent), a printed pay phrase (enr_pay_text) on 1838 (52.5 percent), and salary_min/salary_max/salary_unit on 0 of 3498: the numeric fields are written as empty for every row (pflege_jobs/sources/inbox.py), though schema.org JobPosting pages carry baseSalary on some boards and many texts print a figure. Any pay signal at all: 52.6 percent. Wanted: (a) a printed figure becomes numbers with unit (month, year, hour) and the quoted phrase as evidence, from baseSalary where the page has it and from the text otherwise; (b) the tariff tag keeps its pattern evidence; (c) where the posting carries no signal but the clinic's operator or traegerart implies a tariff, the clinic-level default of TASK-108 applies and is shown as a clinic claim, never as the posting's own statement; (d) every pay value shows its source (posting, page data, clinic default). Fits the claim-and-evidence catalogue of TASK-441. Related: TASK-108 (clinic default tariff), TASK-452 (candidate-side salary lookup), TASK-184 (pay as a landscape dimension).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 salary_min, salary_max and salary_unit are filled from baseSalary and from a printed figure, each with the quoted phrase as evidence, and a red test per source shape on the mirror
- [ ] #2 The tariff tag and the pay grade keep their evidence; a comparison phrase never counts as the posting tariff (as today)
- [ ] #3 A clinic default tariff exists as a claim of the clinic (TASK-108) and is shown apart from the posting own value, with its source
- [ ] #4 Counts before and after by signal and by source are given to Ivan; nothing is written to the database before he approves the exact counts
<!-- AC:END -->
