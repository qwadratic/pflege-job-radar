---
id: TASK-94
title: >-
  Live services run straight from the working tree, so a scheduled run firing
  mid-edit executes half-written code
status: To Do
assignee: []
created_date: '2026-09-21 07:58'
labels: []
dependencies: []
ordinal: 94000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Observed 2026-09-21. deploy/pflege-web.service and deploy/pflege-hunter.service both set WorkingDirectory=/home/exedev/repo and run .venv/bin/... directly against the checkout. app/scheduler.py fires the adapter crawl at 03:00 and a verify pass afterwards, in that same process tree.

While a multi-agent fix workflow was editing crawlers/, pflege_jobs/ and app/ between roughly 02:00 and 07:00, scheduled run 109 (mode=verify, 05:53) failed with 'TypeError: unhashable type: list' after processing 2,384 open postings. The same crash does not reproduce against the current tree: the offline suite is green at 1259 passed, and direct probes of extract_location with list-valued addressLocality and numeric postalCode both return correctly. So the most likely explanation is that the run imported a partially-written module set -- but that is a hypothesis, and the alternative (a real bug on a path no probe has hit) cannot be ruled out from the log line alone.

Either way the underlying exposure is real and independent of this one crash: there is no separation between 'the code being edited' and 'the code production runs', so any edit window overlapping 03:00 or a verify pass can corrupt a live run, and the resulting failure is indistinguishable from a genuine bug.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Scheduled runs execute from a state that cannot change underneath them -- a deployed copy, a git ref checked out at run start, or an equivalent -- rather than whatever happens to be in the working tree at that instant
- [ ] #2 The run record captures which code version executed (commit sha or equivalent), so a failure can be attributed to a version instead of guessed at
- [ ] #3 Run 109's 'unhashable type: list' is either reproduced and fixed, or explicitly closed as a mid-edit artifact once versioned runs make the distinction possible
<!-- AC:END -->
