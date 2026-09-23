---
id: TASK-121
title: >-
  Curate up to 3 photos per clinic from the already-collected archive
  (data/clinic_photos.zip)
status: To Do
assignee: []
created_date: '2026-09-23 00:36'
labels: []
dependencies: []
ordinal: 121000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-120's pilot run already collected up to 6 raw candidate photos per clinic (search + Maps + site,
405/407 clinics, archive at data/clinic_photos.zip, ~2216 files) -- see TASK-120's implementation
notes for the full collection history (4 pilot rounds, quality fixes). That archive is raw candidates,
not curated: no scoring, no dedup beyond a simple perceptual hash, no rejection reasons.

Ivan 2026-09-23: wants this narrowed to the best up to 3 photos per clinic (no near-duplicates),
using what's already sitting in the archive rather than re-collecting. Filed now specifically so it
is not forgotten while other clinic-photo work (the single Maps-photo table/API build, the clinic
paragraph pilot) proceeds first -- Ivan asked explicitly to leave this task open/unimplemented for
now.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The 1-6 raw candidate photos per clinic already in data/clinic_photos/<clinic_id>/ are scored/filtered down to up to 3 best, no two near-duplicate (reuse the existing _ahash/_hamming dedup from tools/task120_collect_clinic_photos.py or equivalent)
- [ ] #2 A rejection reason is recorded for candidates not chosen, not just silently dropped (matches TASK-120 AC#3's own convention)
- [ ] #3 Result feeds the same clinic_photos table/API TASK-120 is building, without breaking whatever schema that work lands with
<!-- AC:END -->
