---
id: TASK-198
title: >-
  Registry data found by the TASK-185 replay: 16 careers_url lint findings,
  shared and twin clinics that the matcher cannot tell apart
status: To Do
assignee: []
created_date: '2026-10-01 23:18'
labels:
  - registry
  - data
dependencies: []
priority: medium
ordinal: 195000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-185 pipeline replay (agent A1, 2026-10-01) and registry_lint found registry rows that make attribution impossible without a data decision; code cannot fix them. (1) careers_url that is a posting or a slug, not a board: job-slug 16268 47102 47601 67201 67601; stellenangebote-slug 77902 77903; posting-url 16221 16230 16236 16241 56409 56413 RH1895 RH2081 (some may be real one-page boards: check each site first-hand); the aggregator row 66103 is covered by the kp24 change set. (2) 36102 shares 36101's careers_url (karriere.klinikum-amberg.de): 13 postings tie (6090 6091 6092 6093 6094 6906 6907 6908 6909 6911 6912 10446 12721). (3) Malteser twin RH1842 (jobs.malteser.de): operator-brand words name both sites, 12 postings (6601 6603 6604 6605 6606 6608 6610 6611 12316 12317 12318 12319). (4) barmherzige.net care-home ads (St. Elisabeth Teisendorf, St. Hildegard Siegsdorf ...) are stored on 16214; the link stage moves them to 16219 Neuwittelsbach by R1_exact on the filed-under employer; neither is right (10080 10081 10082 10084 10087 10089 10091 10092 10093 10095 10100 10711): the registry lacks the care homes or they belong in Altenpflege. (5) Other twins: Mainkofen RH2143 / Wartenberg 11 postings, Klinikum am Europakanal vs RH2720 6, Prien RH2091 1, TUM 16291/16292 3 (40 right TUM links depend on the employer the posting is filed under). Evidence per posting: job dir tmp/A1_work/S_good_v9.tsv and change_summary_v9.txt.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each of the listed clinics is checked against its own website (first-hand evidence recorded) and its careers_url, status or a missing sibling is corrected through tools/apply_clinic_corrections.py with a reason code and evidence, or left with a written reason
- [ ] #2 After the corrections the TASK-185 replay (tools/replay_matcher.py) is re-run and the postings of items (2)-(5) attach to a site the text names, or stay unmatched with a note
- [ ] #3 python -m pflege_jobs.registry_lint reports no finding that was not decided
<!-- AC:END -->
