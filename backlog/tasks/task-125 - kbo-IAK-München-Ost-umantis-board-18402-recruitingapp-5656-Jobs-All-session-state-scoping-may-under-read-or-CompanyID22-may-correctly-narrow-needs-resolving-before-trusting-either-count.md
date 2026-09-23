---
id: TASK-125
title: >-
  kbo-IAK München-Ost umantis board (18402/recruitingapp-5656): /Jobs/All
  session-state scoping may under-read or CompanyID=22 may correctly narrow --
  needs resolving before trusting either count
status: To Do
assignee: []
created_date: '2026-09-23 08:22'
labels: []
dependencies:
  - TASK-123
priority: medium
ordinal: 125000
---

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Determine what CompanyID query param scopes to on recruitingapp-5656.de.umantis.com: same legal entity/site as clinic 18402 (München-Ost), or a UI-only department/tab filter that has no bearing on which postings genuinely belong to this clinic
- [ ] #2 Confirm whether the 5 postings visible only on an unscoped fresh-session /Jobs/All fetch (ids 3134/3215/3228/3271/3329/3343/3346 minus overlap) already appear under a different registry clinic_id via kbo-iak.de's own wp_jobs-routed board, or are genuinely unclaimed
- [ ] #3 Fix career_crawl.py's Crawler so a CompanyID-scoped seed page fetched earlier in the same requests.Session cannot silently narrow a later /Jobs/All fetch in that same session (either fetch /Jobs/All first, or use an isolated session for it) -- only if AC1/AC2 show the narrower scope is wrong
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Found live 2026-09-23 during TASK-123 AC3 measurement (not a GENDER/JOB_TEXT defect -- all 10 candidate titles on a FRESH-session /Jobs/All are correctly gendered; the gap is umantis' own session-pinned CompanyID scoping). Fresh session fetching plain https://recruitingapp-5656.de.umantis.com/Jobs/All returns 10 distinct /Vacancies/<id>/Description/1 links; the production seed (ats_seeds.BUILDERS['umantis']) visits Jobs/1..5 with ?CompanyID=22&Reset=G first, which appears to pin the session, so /Jobs/All later in that same crawl only yields 5. All 12 postings checked (5 kept + 7 missing) share identical generic meta keywords (kbo-Kinderzentrum boilerplate), so title/keyword text gives no signal on which legal entity/site each belongs to -- this needs either umantis-side documentation/support contact or cross-referencing kbo-iak.de's own separately-routed board (clinics 16251/16252/17803/16257/16263, all ats_type=wp_jobs pointed at kbo-iak.de) to see if the missing 7 already surface there under a sibling clinic_id.
<!-- SECTION:NOTES:END -->
