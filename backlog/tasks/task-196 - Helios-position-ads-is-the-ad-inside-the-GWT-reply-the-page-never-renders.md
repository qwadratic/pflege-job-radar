---
id: TASK-196
title: 'Helios position ads: is the ad inside the GWT reply the page never renders?'
status: To Do
assignee: []
created_date: '2026-10-01 21:01'
labels:
  - crawler
  - pi_asp
  - helios
dependencies: []
ordinal: 193000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-184 A3 (UNVERIFIED): Helios position pages are the application form only (the ad lives on helios-gesundheit.de), so 94 stored Helios rows carry no text. The GWT replies of 6 probed Helios positions are 176-187 kB, the size of regiomed replies that DO carry the ad (8 probed, 169-201 kB, ads 1.8-3.4 kB). The ad may be inside the reply. If so decode it; if not, the 27 helios-gesundheit.de www rows (Firecrawl, real ad text) are the only source of Helios ad text and agent D's duplicate set S1 must not retire them.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Decide from one reply: ad text present in the GWT payload yes/no, with the byte position and the first 200 characters
- [ ] #2 If yes: the P&I adapter stores it (frozen reply fixture, red-green); if no: this task records it and closes
<!-- AC:END -->
