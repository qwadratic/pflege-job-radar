---
id: TASK-448
title: >-
  A corrected place does not reach stored postings: after the first run on the
  fixed code 11 of 14 postings keep their old city
status: To Do
assignee: []
created_date: '2026-10-07 14:44'
updated_date: '2026-10-08 11:55'
labels:
  - crawler-coverage
  - data-quality
dependencies: []
priority: medium
ordinal: 328000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-07 by pflege-clawl while closing TASK-431.9. After PR #27 (place fixes) and run 242 (first run on the new code, boards of the corrected postings), the LINK corrections hold (41 relinks, 271 unlinks, 33 retires: 0 drift), but the CITY of stored postings did not follow the new adapter output: kbo head-office stamp, 5 postings (3 open, 2 expired) still carry the city Muenchen where the fixed adapter now gives no place; umantis ANregiomed, 9 postings with a city change in the executor's set: 3 now equal the proposed town, 6 open ones still show Ansbach where the posting's site chip names Rothenburg. Measured with a read-only comparison of live postings.city against the set's proposed value; run 242 did visit those boards (kbo and ANregiomed boards finished at 10:21 to 10:31 UTC). Hypotheses, not verified: (a) a later observation without a place does not overwrite a stored place (the ingest upsert keeps non-null values); (b) resolve_postings picks the city from the most authoritative observation (city = coalesce(city_override, chosen observation city)), so an older observation with the seed town can outrank the new one. A manual override (postings.city_override, as tools/reverify_and_clean.py does) can set a town but cannot clear one (coalesce). Needed: find which of (a) or (b) holds with one posting traced from observation to postings.city; decide where the fix belongs (ingest, resolve, or a one-off correction through the corrections ledger); a red test first. Not done: any change to postings.city.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 One kbo posting and one umantis posting traced from the crawl row through posting_observations to postings.city, with the rule that keeps the old city written down
- [ ] #2 The fix is at the source (ingest or resolve) or recorded as a one-off correction with ledger rows, with a red test first on the mirror or a fixture
- [ ] #3 The 5 kbo and 6 umantis postings show the proposed place (or none) after the fix, measured live
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-10-08 FIRST TRACE (pflege-clawl, read only, one posting each). The two hypotheses of the description are NOT confirmed. umantis, posting 10470: its newest observation (2026-10-08 05:35, run 243, new code) itself carries city Ansbach, so the adapter output on the live page is still Ansbach, not Rothenburg; no overwrite problem, the fix of the site chip does not reach this live page (the executor test is green on the recorded mirror page): compare the live page with the mirror page, the text rung (tm: in/am/Standort + town) may win before the chip branch in career_crawl. kbo, posting 6622: its only observation is from 2026-09-24 (last_seen 2026-09-24), the posting is no longer on the board but still status open: the kbo head-office stamp is moot for the 3 open ones, the real defect is a posting that was not seen for 14 days and is not retired (not checked whether verify 244 looked at it). Re-scope: (1) umantis live-vs-mirror difference with a red test on a re-recorded page, (2) why an open posting last seen 14 days ago on a walked board is not retired, (3) the 5 kbo and 6 umantis postings. The ACs below stay valid for the outcome.
<!-- SECTION:NOTES:END -->
