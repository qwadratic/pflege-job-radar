---
id: TASK-91
title: >-
  app/data.py swallows a snapshot build failure, keeps the stale snapshot and
  resets the TTL
status: To Do
assignee: []
created_date: '2026-09-21 04:27'
labels: []
dependencies: []
ordinal: 91000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the top-100 coverage audit 2026-09-21 while it was working, not looked for: this fired on roughly 1 in 4 adapter runs across three independent audit batches and cost the auditors real time.

app/data.py:351-355 catches an exception from _build(), stores it in _snap['error'], KEEPS the stale or empty snapshot, and RESETS the TTL. A transient v_postings 500 therefore surfaces everywhere downstream as 'unknown clinic_id <id>' -- and because the TTL was reset, the bad state persists for a full cycle instead of being retried on the next call.

This is a silent fallback of exactly the kind CLAUDE.md's 'No safety nets' rule forbids, and unlike the crawler-side instances already fixed in TASK-62..76, this one is in the live API path, not a CLI.

Second, related instance in the same class: crawlers/vendor_adapters.py:550 prints 'no job links in sitemap' to stderr and then returns a partial result reported as success. Three boards in the audit returned 2 / 6 / 73 rows where 27 / 53 / 1,122 exist. That log line is a ready-made failure signal that nothing records or alerts on (see TASK-85 AC#1 for the crawl-side fix; this task covers making the failure visible in the API/observability layer rather than only in the crawler).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A snapshot build failure does not reset the TTL and does not silently serve a stale or empty snapshot as if it were current: the failure is surfaced to the caller and retried on the next request
- [ ] #2 Callers that today see 'unknown clinic_id <id>' during a transient failure instead get an explicit error, so the condition is distinguishable from a genuinely unknown clinic
- [ ] #3 A test pins the failure path: a raising _build() must not leave a reset TTL plus a stale snapshot
- [ ] #4 The repo is swept for the same shape -- except branch that stores an error, keeps prior state and resets a cache/TTL -- and each instance found is either fixed or justified in writing
<!-- AC:END -->
