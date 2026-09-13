---
id: TASK-88
title: >-
  Fix 5 pre-existing offline test failures (2 unstubbed Supabase calls, 3 stale
  from TASK-56)
status: Done
assignee: []
created_date: '2026-09-13 17:13'
updated_date: '2026-09-13 17:13'
labels: []
dependencies: []
ordinal: 88000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Investigated the 5 tests that had been failing in the offline suite throughout this session's WA harness work, unrelated to it. Two (test_problem_json_404, test_owner_only_passes_after_login[GET-/api/inbox]) hit live Supabase because job_detail()/inbox_summary() were never stubbed in those specific tests, unlike every other test exercising them. Three (test_career_crawl_section.py) were stale against TASK-56 (commit 0ee9828), which deliberately changed section-first crawling from exclusive-subtree-only to always-merge-with-full-walk after that exclusive scoping silently dropped 93/102 real postings on a live board -- the tests were never updated to match and TASK-56's own notes flagged this as a known gap.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 test_problem_json_404 and test_owner_only_passes_after_login[GET-/api/inbox] stub the Supabase-hitting call they were missing, matching this repo's established no-network-in-offline-tests convention
- [x] #2 A real bug found while fixing the stale tests: TASK-56's merge-dedup in career_crawl.py kept the full-walk's row over the section walk's row on a duplicate URL, backwards from its own stated top-up intent -- silently discarding the nursing_section_confirmed signal for nearly every job in practice, since the section link is normally reachable from the full walk's own seed page. Fixed: section_rows now win on a duplicate URL.
- [x] #3 test_section_first_scopes_walk_to_the_nursing_subtree renamed and rewritten to assert the current (TASK-56) contract instead of the pre-TASK-56 exclusive-scoping behavior it was still asserting
- [x] #4 test_section_first_falls_back_to_full_walk_when_subtree_is_empty's stats["section_first"] assertion corrected to match what that field actually reports (a section link was found at all, not whether the subtree yielded rows)
- [x] #5 Full offline suite is green: 1152 passed, 0 failed
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Delegated to a background agent given the 5 failures were unrelated to the WA harness work in progress; reviewed the full diff myself before committing (trust but verify) -- all four changed files read correctly, the career_crawl.py fix is minimal and well-commented, the TASK-56 commit (0ee9828) it references is real and matches the description. Re-ran the full suite independently and confirmed 1152 passed, 0 failed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closed out a backlog of 5 stale/broken offline tests that predated this session's WA harness work. Two were simple missing-stub fixes; the career_crawl.py investigation surfaced and fixed a real, previously-undetected bug in TASK-56's merge logic that was silently discarding the nursing-section classification signal for almost every job crawled through it.
<!-- SECTION:FINAL_SUMMARY:END -->
