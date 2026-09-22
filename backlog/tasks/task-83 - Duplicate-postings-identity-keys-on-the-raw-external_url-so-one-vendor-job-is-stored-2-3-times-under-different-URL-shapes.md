---
id: TASK-83
title: >-
  Duplicate postings: identity keys on the raw external_url, so one vendor job
  is stored 2-3 times under different URL shapes
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:25'
updated_date: '2026-09-22 02:59'
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
- [x] #1 external_url is canonicalized to the vendor job id before the posting dedup key, in ONE place, covering at minimum softgarden, dvinci, umantis, helix and b-ite URL shapes
- [ ] #2 The 86 existing duplicate rows are merged, not just prevented going forward; report the before/after open_jobs total and per-clinic counts for Bayreuth, Bamberg, Fürth, Neumarkt and Kempten
- [x] #3 A test pins each canonicalization shape with a real URL pair from the live data
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Found the real dedup mechanism: posting_observations has unique(source_id, source_ref) (sql/001_schema.sql:112) with ON CONFLICT upsert (pflege_jobs/sinks.py) -- two URL-shape variants of one job become two rows only because source_ref/external_url is the raw, uncanonicalized url.
2. Added classify.canonical_job_url(url) covering softgarden/dvinci/umantis/helix/b-ite vendor-job-id shapes (host-scoped where the vendor's own id is per-tenant: dvinci/helix/umantis; not scoped where it's platform-global: softgarden/b-ite -- verified against real production URL pairs).
3. Wired it into career_crawl.py's _base() as source_ref (external_url/source_url stay the real, as-crawled link -- verify.py only ever fetches those, never source_ref).
4. For vendor_adapters.py's crawl_dvinci (owned, dedicated adapter -- NOT routed through career_crawl.py, so step 3 doesn't reach it): normalized jobPublicationURL to the id-only form directly in parse_dvinci, verified LIVE that the id-only form 200s and dvinci's own server redirects it to the slugged page (so this is a real, currently-serving url, not synthetic).
5. crawl_helix (vendor_adapters.py, also owned) deliberately NOT url-rewritten: verified live that dropping the /<unit>/ path segment 404s, so no safe real-URL canonical form exists there; softgarden.py/bite.py (pflege_jobs/sources) are NOT owned this wave, so canonical_job_url can't be wired into them even though the function covers their shapes.
6. Tests: real URL pairs pulled from production (Bamberg/Fuerth/Neumarkt dvinci, Bezirk Unterfranken helix, Diakoneo b-ite; ANregiomed umantis; Bayreuth softgarden id, host pairing constructed per the vendor's documented shape). Mutation-tested via /tmp copies.
7. AC#2 (retro-merge): production write access refused this round -- produced a DRY-RUN report (backups/task83_dedup_dryrun_20260922T024753Z.txt + _pairs_.json) grouping all 2502 currently-open postings by (clinic_id, canonical_job_url(external_url)): 26 groups / -26 rows overall, with Bamberg/Fuerth/Neumarkt each -5 (dvinci) and Bezirk Unterfranken -2/-2 (helix) matching the audit exactly. Bayreuth 0 (already single-host today) and Kempten 0-by-this-rule (real cross-vendor TYPO3-vs-umantis duplication found and documented, deliberately not merged -- false-merge risk, out of owned files) both explained in the report.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Full offline suite (once, at end): 1336 passed, 1 skipped, 0 failed, 1197 deselected (-m "not network"), 342s. Baseline was 1310/1/0 (commit a5c01d6) -- delta includes 13 tests I added plus other concurrent waves' landed work in this shared tree.

Files actually changed by me: pflege_jobs/classify.py, pflege_jobs/sources/career_crawl.py, crawlers/vendor_adapters.py, tests/test_mech_dedupe_key.py, tests/test_career_crawl_section.py, tests/test_vendor_adapters.py. pflege_jobs/patterns.json shows an uncommitted diff in git status but it is NOT mine -- pre-existing (present in my very first backup before I touched anything), looks like TASK-89's work; I made zero edits to it.

AC#2 evidence: backups/task83_dedup_dryrun_20260922T024753Z.txt (+ _pairs_.json with the exact {src,dst} posting_id pairs, same shape pflege_jobs.cli's own merge machinery consumes). Computed by grouping all 2502 currently-open postings by (clinic_id, canonical_job_url(external_url)) using the REAL landed function. 26 groups, -26 rows overall. Bamberg/Fuerth/Neumarkt each -5 (dvinci id-vs-id+slug) matches the audit exactly; Bezirk Unterfranken (helix, clinic 67206/67804) -2/-2 also found live and matches "helix same prj under two board paths". Bayreuth: 0 (single-host today, 39/39 -- not reproducible live now, but the function still folds that shape per its tests). Kempten: 0 by design -- real cross-vendor TYPO3-vs-umantis duplication found and documented in the report (same numeric id across karriere.klinikverbund-allgaeu.de / karriere-im.klinikverbund-allgaeu.de / recruitingapp-5556.de.umantis.com) but deliberately NOT merged: umantis ids are small per-tenant sequentials (two different tenants both have a vacancy "1", verified), so bridging across unrelated host families on id alone risks a false cross-clinic merge; safe bridging needs seed-level tenant knowledge outside classify.py's pure per-URL scope and outside this wave's owned files.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added pflege_jobs.classify.canonical_job_url(url), covering dvinci/helix/umantis/softgarden/b-ite vendor-job-id URL shapes, and wired it as career_crawl.py's source_ref (the actual unique(source_id, source_ref) dedup key, sql/001_schema.sql:112) -- external_url/source_url are left untouched (real, as-crawled links; verify.py never fetches source_ref). Also normalized vendor_adapters.py's crawl_dvinci to the id-only URL form directly (verified live: it 200s and dvinci's own server redirects it to the slugged page). crawl_helix intentionally NOT url-rewritten: verified live that dropping its /<unit>/ segment 404s, so no safe real-URL canonical form exists for it within this wave's files; softgarden.py/bite.py are dedicated adapters outside this wave's ownership, so canonical_job_url isn't wired into them even though it covers their shapes.

AC#1, AC#3 checked: function covers all 5 named vendors, tested against real URL pairs pulled from production, mutation-tested. AC#2 left UNCHECKED: per this round's explicit "do NOT mutate production data" instruction, produced a DRY-RUN report instead of merging (backups/task83_dedup_dryrun_20260922T024753Z.txt) -- 26 duplicate groups / -26 open_jobs found live across the current 2502 open postings, with exact per-clinic before/after for the 5 AC-named clinics and an explanation for the two that show 0 today (Bayreuth already single-host; Kempten's real cross-vendor duplication documented but deliberately not merged, false-merge risk). Task is not genuinely done until either that merge is applied or the scope is accepted as dry-run-only for this round -- left In Progress rather than Done.

Full offline suite: 1336 passed, 1 skipped, 0 failed (-m "not network"), baseline was 1310/1/0.
<!-- SECTION:FINAL_SUMMARY:END -->
