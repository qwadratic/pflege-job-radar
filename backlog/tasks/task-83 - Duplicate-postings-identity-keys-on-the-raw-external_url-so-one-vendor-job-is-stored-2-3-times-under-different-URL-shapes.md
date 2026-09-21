---
id: TASK-83
title: >-
  Duplicate postings: identity keys on the raw external_url, so one vendor job
  is stored 2-3 times under different URL shapes
status: To Do
assignee: []
created_date: '2026-09-21 04:25'
labels: []
dependencies: []
ordinal: 83000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21: 11 clinics, 86 duplicate rows. Inflates every count and makes 'do we cover this clinic' unanswerable from the database.

Posting identity keys on the raw external_url string rather than the vendor's own job id. Confirmed instances:
- softgarden vanity host vs *.softgarden.io: Klinikum Bayreuth's '78 open postings' is really 39.
- dvinci /de/jobs/<id> vs /de/jobs/<id>/<slug>: Bamberg 5, Fürth 5, Neumarkt 5.
- Kempten stores the same umantis vacancy across 3 hosts: 14 duplicates.
- Diakoneo /jobposting/ vs /de/jobposting/: 5. helix same prj under two board paths: 2. Helios UUIDv4 vs UUIDv5: 3. Malteser slug change: 3. Münchberg: 2. medbo re-slug: 3.

The vendor job id is available in every one of these URL shapes; the dedupe key just never extracts it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 external_url is canonicalized to the vendor job id before the posting dedup key, in ONE place, covering at minimum softgarden, dvinci, umantis, helix and b-ite URL shapes
- [ ] #2 The 86 existing duplicate rows are merged, not just prevented going forward; report the before/after open_jobs total and per-clinic counts for Bayreuth, Bamberg, Fürth, Neumarkt and Kempten
- [ ] #3 A test pins each canonicalization shape with a real URL pair from the live data
<!-- AC:END -->
