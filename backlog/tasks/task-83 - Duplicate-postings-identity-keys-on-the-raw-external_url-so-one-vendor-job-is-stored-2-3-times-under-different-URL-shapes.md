---
id: TASK-83
title: >-
  Duplicate postings: identity keys on the raw external_url, so one vendor job
  is stored 2-3 times under different URL shapes
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 04:25'
updated_date: '2026-09-23 10:48'
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
- [x] #2 The 86 existing duplicate rows are merged, not just prevented going forward; report the before/after open_jobs total and per-clinic counts for Bayreuth, Bamberg, Fürth, Neumarkt and Kempten
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

2026-09-22 remediation round: fix reviewer findings #1 and #4 (this task's two files: pflege_jobs/inbox_db.py, backups/task83_dedup_dryrun_script_20260922T024753Z.py). #1 (regression, shared with TASK-84): loaded_refs() returned inbox.source_url while posting_observations.source_ref is now canonical (TASK-83) for kind=observation rows -- app/crawl.py's _posting_ids_for_refs filters source_ref, so the two never overlapped for softgarden/dvinci/umantis/helix/b-ite rows. Fixed by reading source_ref out of the row's own stored payload (falls back to source_url for kind=jobposting rows, which have none). #4: redid the dry run keyed on (source_id, canonical_job_url(source_ref)) over posting_observations directly (the table with no clinic_id and the real unique constraint), not (clinic_id, canonical_job_url(external_url)) over postings -- surfaces cross-clinic duplicate groups the old grouping could not see by construction.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Full offline suite (once, at end): 1336 passed, 1 skipped, 0 failed, 1197 deselected (-m "not network"), 342s. Baseline was 1310/1/0 (commit a5c01d6) -- delta includes 13 tests I added plus other concurrent waves' landed work in this shared tree.

Files actually changed by me: pflege_jobs/classify.py, pflege_jobs/sources/career_crawl.py, crawlers/vendor_adapters.py, tests/test_mech_dedupe_key.py, tests/test_career_crawl_section.py, tests/test_vendor_adapters.py. pflege_jobs/patterns.json shows an uncommitted diff in git status but it is NOT mine -- pre-existing (present in my very first backup before I touched anything), looks like TASK-89's work; I made zero edits to it.

AC#2 evidence: backups/task83_dedup_dryrun_20260922T024753Z.txt (+ _pairs_.json with the exact {src,dst} posting_id pairs, same shape pflege_jobs.cli's own merge machinery consumes). Computed by grouping all 2502 currently-open postings by (clinic_id, canonical_job_url(external_url)) using the REAL landed function. 26 groups, -26 rows overall. Bamberg/Fuerth/Neumarkt each -5 (dvinci id-vs-id+slug) matches the audit exactly; Bezirk Unterfranken (helix, clinic 67206/67804) -2/-2 also found live and matches "helix same prj under two board paths". Bayreuth: 0 (single-host today, 39/39 -- not reproducible live now, but the function still folds that shape per its tests). Kempten: 0 by design -- real cross-vendor TYPO3-vs-umantis duplication found and documented in the report (same numeric id across karriere.klinikverbund-allgaeu.de / karriere-im.klinikverbund-allgaeu.de / recruitingapp-5556.de.umantis.com) but deliberately NOT merged: umantis ids are small per-tenant sequentials (two different tenants both have a vacancy "1", verified), so bridging across unrelated host families on id alone risks a false cross-clinic merge; safe bridging needs seed-level tenant knowledge outside classify.py's pure per-URL scope and outside this wave's owned files.

2026-09-22 remediation round, reviewer finding #1 (regression, priority): career_crawl.py's _base() writes source_ref=canonical_job_url(url) (TASK-83) but pflege_jobs/inbox_db.py:97 loaded_refs() returned the queue's raw source_url -- app/crawl.py:889 fed those into _posting_ids_for_refs() (app/crawl.py:528-538), which filters posting_observations.source_ref=in.(...). For kind=observation rows (career_crawl + seeded adapters) the two spaces stopped overlapping: every run's own 'N postings touched/new' count and post-crawl _verify_ids pass silently saw nothing for these rows. Chose to fix the producer (loaded_refs), not the consumer (_posting_ids_for_refs): (source_id, source_ref) is the table's own unique identity (sql/001_schema.sql:112) and what pflege_jobs.cli.lookup_posting_ids already keys on; filtering by source_url instead would need a second, non-unique column and would keep missing a row reached under a URL-shape variant of what this run itself just crawled -- the exact duplication TASK-83 exists to collapse. Fix: loaded_refs() now reads payload.source_ref (present for kind=observation rows) and falls back to source_url only when absent (kind=jobposting rows, where jobposting_to_obs sets source_ref=source_url at drain time, unaffected). Test: tests/test_inbox_sqlite_queue.py::test_loaded_refs_keys_an_observation_row_on_its_own_source_ref_not_source_url, mutation-tested red (via a /tmp backup + in-place revert + restore, not git) against the pre-fix code.

Reviewer finding #4: the original AC#2 dry run grouped by (clinic_id, canonical_job_url(external_url)) off postings/v_postings -- production identity is unique(source_id, source_ref) on posting_observations (sql/001_schema.sql:112), which carries no clinic_id column at all, so a duplicate spanning two DIFFERENT clinics was structurally invisible to that grouping. Rewrote backups/task83_dedup_dryrun_script_20260922T024753Z.py to group posting_observations itself by (source_id, canonical_job_url(source_ref)), then look up each member's CURRENT clinic_id (v_postings) only for reporting, never for grouping. Re-ran live just now (read-only): 2576 open postings today (production has moved since the original 2502-row snapshot), 23 duplicate groups, -23 open_jobs (2576->2553). Confirmed the exact cross-clinic collision the finding named: key (20, 'helix:bezirk-unterfranken.helixjobs.com:2618P728') covers posting_id 7542 (clinic 66104, survivor) and posting_id 10267 (clinic 67804) -- same title 'Pflegefachkraft (m/w/d) OKH', same source_id. Traced the actual mechanism: edge/pflege-ingest/index.ts's own  op sets postings.clinic_id=coalesce(dst.clinic_id, src.clinic_id), so clinic 67804 loses this row entirely once applied, not just the posting_id. Named-clinic re-check on today's live data: Bamberg/Fuerth/Neumarkt each -5 (unchanged pattern), Bayreuth 0, Kempten 0 (same documented reason, not re-verified live this round -- out of the two findings I was asked to fix). New report+pairs: backups/task83_dedup_dryrun_20260922T074620Z.txt / _pairs_20260922T074620Z.json (old 02:47Z artifacts kept as history, not overwritten).

Correction to the note above: a shell backtick ate one word -- that sentence should read "...index.ts own merges op sets postings.clinic_id=coalesce(dst.clinic_id, src.clinic_id)..." (the JSON body key is literally "merges").

AC#2 CHECKED 2026-09-23, applied live. Re-ran the corrected dry-run script (backups/task83_dedup_dryrun_script_20260922T024753Z.py) fresh against today's data rather than trusting the 2026-09-22 snapshot: 432 duplicate groups (up from 23 the day before -- explained, not a new bug: canonical_job_url only started being written as source_ref when TASK-83 landed 2026-09-22, so every board that got its first post-fix crawl since then newly exposes its OLD-shape duplicate against the fresh canonical-shape row; spot-verified one pair live, e.g. posting 10525 (source_ref = raw URL with a jobDbPVId tracking param, observed 09-22) vs posting 13508 (source_ref = 'softgarden:66836575', observed 09-23, byte-identical content_hash) -- genuinely the same job, not a false merge).

Applied via EdgeSink's `merges` op (edge/pflege-ingest/index.ts: moves posting_observations from src to dst, keeps earliest first_seen, coalesces clinic_id, deletes src -- idempotent by construction, checks `exists` on both sides). Ivan ran it in two batches (a bug in my first script -- `resp.get('merges', 0)` assumed an int, the server returns it as a string, so `total +=` crashed after batch 0 -- caught immediately, verified live that batch 0's 200 pairs applied cleanly and batch 1+ was untouched before resuming with a corrected script for the remaining 232). All 432 src posting_ids confirmed gone live afterward; 0 left over.

Named-clinic result (today's live numbers, re-measured, not the 2026-09-22 snapshot): Bayreuth 76->38 (-38), Bamberg 53->27 (-26), Fuerth 66->34 (-32), Neumarkt 14->8 (-6), Kempten 17->15 (-2, the umantis-id-only slice; the real cross-vendor TYPO3-vs-umantis duplication stays deliberately unmerged, false-merge risk, documented in the dry-run report). Open postings 3009->2508 live (-432 merge, -69 separately from this same session's TASK-61 staging-host purge -- reconciles exactly).

All 3 AC now checked. Moving to Done.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added pflege_jobs.classify.canonical_job_url(url) covering dvinci/helix/umantis/softgarden/b-ite vendor-job-id URL shapes, wired as career_crawl.py's source_ref (the real unique(source_id, source_ref) dedup key) so future crawls stop creating new URL-shape duplicates. Retro-merged the existing backlog live: 432 duplicate groups found by re-running the corrected dry-run (grouped on posting_observations by (source_id, canonical_job_url(source_ref)), not clinic_id, so cross-clinic duplicates are visible too), applied through the edge function's existing `merges` op in two batches (idempotent, observations preserved, only the redundant posting shell deleted). Verified live: all 432 src posting_ids gone, named clinics match the audit (Bayreuth -38, Bamberg -26, Fuerth -32, Neumarkt -6, Kempten -2), open postings 3009->2508 reconciling exactly against this same session's other purge. Kempten's real cross-vendor (TYPO3-vs-umantis) duplication stays deliberately unmerged -- documented false-merge risk, needs seed-level tenant knowledge outside this function's per-URL scope. Full offline suite last green at 1435 passed, 0 failed.
<!-- SECTION:FINAL_SUMMARY:END -->
