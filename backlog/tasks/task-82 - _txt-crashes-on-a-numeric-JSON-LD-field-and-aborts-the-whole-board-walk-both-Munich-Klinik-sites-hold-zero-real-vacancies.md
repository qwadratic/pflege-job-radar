---
id: TASK-82
title: >-
  _txt() crashes on a numeric JSON-LD field and aborts the whole board walk:
  both Munich Klinik sites hold zero real vacancies
status: To Do
assignee: []
created_date: '2026-09-21 04:25'
labels: []
dependencies: []
ordinal: 82000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21. One line, 17 postings, 1,066 beds.

crawlers/vendor_adapters.py:92 _txt() does re.sub(r'<[^>]+>', ' ', s or ''). München Klinik emits "postalCode":81545 as a JSON NUMBER, not a string, so parse_job_page (crawlers/vendor_adapters.py:597) raises TypeError: expected string or bytes-like object, got 'int' on the FIRST posting page it fetches, which aborts the entire board walk. Reproduced live by the audit.

Consequence: clinic 16201 (München Klinik Schwabing) and 16203 (München Klinik Neuperlach, 545 beds) both hold zero real nursing vacancies. 16201's 15 'postings' are all marketing pages, not jobs. Independently confirmed earlier the same day: tools/compare_adapter_fc.py 16203 reports 'adapter: 175 rows' while the database holds 0 for that clinic.

Fix is s = '' if s is None else str(s). The reason this is worth its own task rather than a drive-by is that the same class of bug -- a JSON-LD field arriving as a number, list or dict where the parser assumes a string -- is likely present in the sibling helpers, and a single crash anywhere in a board walk currently costs the whole board with no recorded failure.

Related registry correction from the same audit: 16201 and 16203 should point at https://www.muenchen-klinik.de/stellenmarkt/ rather than /jobs/ -- the full list is inline as 'var allJobs' (57 jobs), so no render rung is needed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 _txt() coerces any non-string scalar rather than raising, and a test pins the numeric-postalCode case with a real JSON-LD fixture
- [ ] #2 Every sibling field reader in parse_job_page that assumes a string is audited for the same numeric/list/dict-shaped input, not just the one that crashed
- [ ] #3 A crash inside one posting page cannot silently abort the whole board walk with a success result: the failure is recorded (crawl_issue) and the walk's outcome reports what it lost
- [ ] #4 16201 and 16203 yield their real nursing vacancies after the fix; report the count for each, and purge 16201's 15 marketing-page rows
<!-- AC:END -->
