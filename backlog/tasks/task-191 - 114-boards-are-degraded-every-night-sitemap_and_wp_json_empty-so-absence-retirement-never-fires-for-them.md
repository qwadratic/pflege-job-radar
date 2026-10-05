---
id: TASK-191
title: >-
  114 boards are degraded every night (sitemap_and_wp_json_empty), so
  absence-retirement never fires for them
status: To Do
assignee: []
created_date: '2026-10-01 18:29'
updated_date: '2026-10-01 18:35'
labels:
  - crawler
  - verify-freshness
dependencies: []
priority: high
ordinal: 188000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-10-01 by the TASK-184 adapter re-run (agent report, then checked in data/app.sqlite crawl_issues by hand). app/runs.py board_walk_ok refuses to treat a posting's absence as evidence when the board has a crawl_issue of kind vendor, seeded, truncated, degraded or incomplete for that day (TASK-87 / TASK-85 / TASK-88). On 2026-10-01 115 boards carry kind=degraded: 114 with 'fell back to a lower-confidence discovery path: sitemap_and_wp_json_empty' (vendor wp_jobs 82, typo3_jobs 18, self_hosted 8, oracle 3, coveto 2, personio 1, talention 1 in the 115 rows) and 1 with 'posting page(s) crashed while parsing: AttributeError: str object has no attribute get'. 107 of the 115 were degraded the night before too, 132 distinct boards since 09-25: the flag is permanent for these boards, so a posting that left the board is never retired by the walk (seeded boards have no absence-retirement code at all). Effect measured in the same review: 606 open postings (StepStone excluded) have last_seen before 2026-09-30, among them Straubing 6 empty shell pages that still answer HTTP 200, Nuernberg 7, Malteser 4 old-slug duplicates, karriere-im.klinikverbund-allgaeu.de 12. The clinic-scoped verify pass skips clinic_id NULL rows unless scope=all and cannot see a 200 shell page. Some degraded boards read the whole board through the fallback (AMEOS: hr4you fallback found 734 real rows), so the flag is not always a real read deficit. Related: TASK-87 (Done), TASK-85, TASK-88, TASK-165 AC3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 For each of the 114 boards: is the fallback read complete? Compare with the boards own self-reported total (TASK-88 method) or a second read path; verdict recorded per board
- [ ] #2 Boards whose fallback read is complete are no longer flagged degraded, boards that really under-read keep the flag with a named cause; absence-retirement then fires for the complete ones with the TASK-87 safeguards; red-green with a fake board, mutation-checked
- [ ] #3 The AttributeError crash on one board is found and fixed with a test (the board url is in crawl_issues of 2026-10-01)
- [ ] #4 A 200 shell page (title only, no job text) is recognised by verify as not a vacancy instead of live
- [ ] #5 verify recognises a deactivated Softgarden job (JobPosting JSON-LD gone) and a removed Oracle requisition as gone; bite and softgarden boards get an absence-retirement path under the same TASK-87 safeguards
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
The AttributeError board: https://www.wessel-gruppe.de/stellenangebote?company_name=kurkl%c3%a4uer+bergbad (wp_jobs, run 225, 2026-10-01).

2026-10-01, same diagnostics: bite and softgarden boards never retire absent postings (app/crawl.py _fetch_board board_absent_gone only under kind=='vendor'); verify.decide() calls a deactivated Softgarden job live (HTTP 200, same visible text, JobPosting JSON-LD gone: 50 of 50 UKA stale pages probed live, 8 of 8 fresh ones still have the JSON-LD) and the Oracle SPA answers 200 for removed requisitions (3 Sana rows). Add both to the verify work here.
<!-- SECTION:NOTES:END -->
