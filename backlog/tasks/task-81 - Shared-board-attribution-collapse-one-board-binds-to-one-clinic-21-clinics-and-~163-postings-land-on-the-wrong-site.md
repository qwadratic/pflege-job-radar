---
id: TASK-81
title: >-
  Shared-board attribution collapse: one board binds to one clinic, 21 clinics
  and ~163 postings land on the wrong site
status: To Do
assignee: []
created_date: '2026-09-21 04:25'
labels: []
dependencies: []
ordinal: 81000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, ranked the single largest defect by postings cost: 21 clinics, 7,599 beds, ~163 postings. 211 postings are already in the database but on the wrong clinic_id or on NULL -- no crawling needed to recover them, only correct attribution.

Four distinct sub-mechanisms, four different fixes:

1. app/crawl.py:695 binds an entire shared board to b['clinics'][0] (grouping at crawlers/routing.py:150). Affects 26108, 76110, 67705, 18801 (+3 siblings at zero), 17701 -- about 39 postings. Worst single case: 26108 LA-Regio Kliniken Landshut, 862 beds, 0 postings, whose entire 34-vacancy board is filed under its 120-bed paediatric sibling 26103 because both registry rows carry the identical careers_url.

2. pflege_jobs/registry.py:142 R1_exact returns on a unique employer-name hit with NO town gate, unlike its own R1_exact_town sibling two lines below. Affects 46203, 46204, 47802, 27705 -- 22 postings, 1,208 beds, 4 clinics go 0 -> correct.

3. A row inherits the seed clinic's town/name when the job page carries no city (app/crawl.py:545), and R0_board_name/R0_board_town (pflege_jobs/registry.py:238) then match it straight back to the seed. Affects 77401, 18501, 18301, 17101, 16107, 17704 -- about 93 postings, plus 48 non-Bavarian rows wrongly stamped Bavarian.

4. pflege_jobs/registry.py:91-95 _pick_site resolves a two-clinic tie on one board by max beds, permanently (46103, 28 postings -- but see the caveat below).

Every one of these boards publishes a per-posting location we are not reading: softgarden jobLocation.address.addressLocality/streetAddress, mein-check-in's town in the title, InnKlinikum's 'Einsatzort:' cell, kbo's jobSite Solr facet, b-ite's address.city.

Caveat from the audit itself: 46103's '28 missing' may be zero real loss -- no nursing posting on that board currently names Michelsberg, so the risk is an empty clinic page, not lost vacancies. Do not fund a _pick_site change on that number alone.

Likely upstream contributor: TASK-80 (the matcher reads a stale CSV missing 117 clinics' careers_url, so board rules cannot fire). Check TASK-80 first -- some of this cluster may resolve without touching the Matcher.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Shared-board rows are attributed per posting from the board's own location field rather than from clinics[0]; name the field read for each affected vendor
- [ ] #2 R1_exact is gated on town the same way R1_exact_town already is: when the posting's city is known and disagrees with the single candidate, fall through instead of matching
- [ ] #3 A row whose city was inherited from the seed clinic cannot be matched back to that seed by R0_board_name/R0_board_town -- the existing _emp_inherited/city_source markers already carry the needed provenance
- [ ] #4 Re-run attribution over existing postings (no re-crawl) and report the before/after clinic_id distribution for the named clinics: 26108, 46203, 46204, 47802, 27705, 77401, 18501, 18301, 17101, 16107, 17704, 76110, 67705, 18801, 17701
- [ ] #5 26108 LA-Regio Kliniken Landshut holds its own adult-care postings and 26103 holds only paediatric ones, verified against the live board
- [ ] #6 The 48 non-Bavarian rows wrongly stamped Bavarian via seed-city inheritance are identified and corrected
<!-- AC:END -->
