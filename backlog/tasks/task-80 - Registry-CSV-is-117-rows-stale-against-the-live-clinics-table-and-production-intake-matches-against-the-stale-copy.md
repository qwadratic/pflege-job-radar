---
id: TASK-80
title: >-
  Registry CSV is 117 rows stale against the live clinics table, and production
  intake matches against the stale copy
status: To Do
assignee: []
created_date: '2026-09-21 04:24'
labels: []
dependencies: []
ordinal: 80000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found 2026-09-21 while validating the top-100 coverage audit's own inputs.

data/registry/clinics.csv is missing careers_url for 117 of 399 active clinics (11,908 beds) that DO have one in the live pflege_jobs.clinics table, and 30 more rows carry a different careers_url than live. Only 1 clinic (4 beds) genuinely has no board URL anywhere.

This is not cosmetic. app/crawl.py:857 calls _cli(["inbox"]) with no --clinics argument, so pflege_jobs/cli.py:511's default kicks in and every production intake run builds its Matcher registry from this stale CSV. The Matcher's board-based rules (R0_board, R0_board_name, R0_board_town, R0_board_tokens in pflege_jobs/registry.py) key on careers_url, so for those 117 clinics the board rule can never fire -- the posting either falls through to a weaker name/town rule and lands on the wrong sibling, or returns None and lands clinic_id=NULL.

Measured: 19 of the 176 currently-unattributed open postings sit on a board belonging to one of these CSV-blind clinics (jobs.schoen-klinik.de 8, kliniken-gz-kru.de 4, bezirk-unterfranken.helixjobs.com 2, frg-kliniken.de 2, and four more). The misattributed-rather-than-null share is larger but not separately measured yet -- the top-100 audit's M1 'shared-board attribution collapse' cluster (21 clinics, 163 postings) is very likely fed by this.

Also relevant: pflege_jobs/cli.py:509's link-clinics defaults to the same CSV, and app/config.py:20 CLINICS_CSV is read by pflege_jobs/mechanics.py:20. app/data.py's clinics() reads the LIVE table via snapshot(), so the crawl-planning side and the matching side disagree about what the registry is.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Intake's Matcher registry is built from the same source of truth the crawl planner uses (the live clinics table), or the CSV is regenerated from live as a pipeline step before intake runs -- one registry, not two
- [ ] #2 Re-run intake after the fix and report how many of the 176 currently-unattributed open postings become attributed, and how many previously-attributed postings change clinic_id (the second number matters: a change means they were previously wrong)
- [ ] #3 A test pins that the registry the matcher sees contains a careers_url for every clinic that has one live, so this drift cannot silently return
- [ ] #4 Decide and document whether data/registry/clinics.csv remains a tracked artifact at all, or becomes a generated file with a regeneration command
<!-- AC:END -->
