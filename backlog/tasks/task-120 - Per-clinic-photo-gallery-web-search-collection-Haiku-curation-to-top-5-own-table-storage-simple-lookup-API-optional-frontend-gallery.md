---
id: TASK-120
title: >-
  Per-clinic photo gallery: web-search collection, Haiku curation to top 5, own
  table + storage, simple lookup API, optional frontend gallery
status: In Progress
assignee: []
created_date: '2026-09-22 21:19'
updated_date: '2026-09-23 00:36'
labels:
  - frontend
  - pipeline
  - new-feature
dependencies: []
ordinal: 120000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Ivan 2026-09-22: clinics currently have no photos anywhere on the board. Wants a script runnable over the whole registry (399 clinics): for each clinic, web-search for candidate photos (building/campus, not stock/logo), pull ~10 candidates, have a Haiku classifier pick the best up-to-5 (quality/relevance), store those in their own table plus a directory on disk, and expose a simple API endpoint that returns a clinic's photos. Frontend gallery on the clinic page is a nice-to-have, explicitly lower priority than the pipeline itself -- do the pipeline first. Prefer quality over exhaustiveness: 1-5 good photos per clinic beats a full 5 of mediocre ones.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A research/design pass picks the web-search source (candidate: Firecrawl search, already used elsewhere in this codebase for Firecrawl agent work; evaluate against a plain search API) and states cost per clinic and total cost for 399 clinics before running it at scale
- [ ] #2 A script takes a clinic (name, town, careers_url/website) and returns up to 10 candidate photo URLs from web search, runnable standalone per clinic and in a loop over the whole registry
- [ ] #3 A Haiku-based classifier scores/filters the 10 candidates down to up to 5 -- picks real building/campus photos over logos, stock photography, team headshots, or unrelated images, with the rejection reason recorded, not just silently dropped
- [ ] #4 Chosen photos are stored in their own table (schema: clinic_id, url or local path, source, score/reason, fetched_at) plus saved to a directory on disk -- decide and document whether the API serves the original remote URL or a locally-cached copy
- [ ] #5 A simple GET endpoint returns a clinic's stored photos by clinic_id
- [ ] #6 Run over a small pilot batch first (e.g. 10-20 clinics), spot-check the picks manually, before running the full 399-clinic pass
- [ ] #7 #7 A combined GET endpoint ('expose', like a real-estate listing) returns one clinic's photo (from AC#5) + its prepared presentation paragraph (from the blurb work) in one response, so a bot integration (e.g. the WhatsApp nurse funnel) can pull a ready-made clinic presentation in a single call
- [ ] #8 #8 The clinic's own card/page on the frontend surfaces this same photo+paragraph data, as its own section distinct from the clinic's open jobs list
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
2026-09-23 (partial pass, per Ivan): add clinic_photos table (app/runs.py) + record_clinic_photo/clinic_photo_url/clinic_photos_map; app/data.py._build() sets c['photo_url']; GET /photos/{clinic_id} in app/main.py serving cached bytes; new seed script (or extended task120 script) does ONE Maps photo per clinic via existing collect_from_maps, saved as maps.<ext>, full 407-clinic run; docs/api.md updated; tests + mutation-test; frontend verified only, no edit expected (clinicPhoto() already reads photo_url).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-23, Ivan: wants the photo (AC#5) and the researched clinic paragraph combined into one
"expose"-style API response (real-estate-listing term -- one call returns a ready presentation: photo
+ text) so a bot (the WhatsApp nurse funnel, see memory pflege-recruiting-business) can hand a
candidate a complete clinic presentation without a second lookup. Also wants this same combined data
on the clinic's own frontend card, as its own section separate from the clinic's jobs list.

Sequencing: a background workflow (wf_0e9b2194-48a) is already building AC#4/#5's table + a single
Maps photo per clinic + the plain photo GET endpoint + docs, and separately piloting the researched
paragraph (Firecrawl-backed, honest-facts-only, German) on 15 clinics for a manual quality check
before scaling to all clinics -- the blurb is NOT yet stored anywhere permanent, still pilot-only.
AC#7/#8 (the combined endpoint + frontend card) are the natural next step ONCE that pilot is reviewed
and the paragraph work is scaled to the full registry -- not implemented yet, added here so the
requirement isn't lost. Do not start AC#7/#8 until the blurb pilot's results have been reviewed.
<!-- SECTION:NOTES:END -->
