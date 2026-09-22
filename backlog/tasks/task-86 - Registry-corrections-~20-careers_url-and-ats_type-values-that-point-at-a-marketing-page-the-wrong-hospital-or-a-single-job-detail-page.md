---
id: TASK-86
title: >-
  Registry corrections: ~20 careers_url and ats_type values that point at a
  marketing page, the wrong hospital, or a single job-detail page
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-22 06:37'
labels: []
dependencies: []
ordinal: 86000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21 verified each of these live. Applying them recovers roughly 34 postings and stops 66301's 15 rows from decaying at the next reconcile. Apply to the LIVE pflege_jobs.clinics table (and see TASK-80 about the CSV that intake actually reads).

Replaces a null, a marketing page, the wrong hospital, or a single job-detail page:
- 66101 -> https://jobs.klinikum-ab-alz.de/Jobs (replaces /karriere/; Knockout SPA, 62 jobs, needs the TASK-85 adapter)
- 27106 -> https://www.dik-karriere.de/stellenangebote (current value points at a DIFFERENT hospital, bkh-landshut.de)
- 16201, 16203 -> https://www.muenchen-klinik.de/stellenmarkt/ (replaces /jobs/; full list inline as 'var allJobs', 57 jobs, no render needed)
- 16215 -> https://www.rotkreuzklinikum-muenchen.de/stellenangebote/ (current value is the association's board: 18 elderly-care jobs, 0 hospital jobs)
- 56404 -> https://www.diakoneo.de/karriere/stellenportal/stellen-in-der-pflege (current /karriere/ page carries no b-ite widget)
- 76201 -> https://www.kliniken-oal-kf.de/karriere/karriereportal/stellenangebote?selection3=3 (walk &selection1=1&page=N until 0 detail links)
- 16214 -> https://karriere-barmherzige-muenchen.de/stellenangebote?tx_oycimport_list%5Bcategory%5D=15 (union the 4 ?...[schedule]=1..4 variants for all 11 Pflege rows)
- 36202 -> https://csj.de/beruf-und-karriere/stellenangebote/alle-stellenangebote (the one page carrying all 27 job links)
- 77406, 76203, 76114 -> https://jobs.bezirkskliniken-schwaben.de/Jobs (one adapter serves the whole operator)
- 66301 -> https://www.kwm-klinikum.de/beruf-chancen/stellenanzeigen/uebersicht-aller-stellen.html, and set ats_type (currently '') -- 51 server-rendered anchors, no JS
- 57408 -> https://jobs.sana.de/de/sites/CX_4025/requisitions?selectedOrganizationsFacet=300000012363401, ats_type=oracle
- 37202 -> https://jobs.sana.de/de/sites/CX_4025/requisitions, ats_type=oracle (currently typo3_jobs, which stores 85 marketing pages as postings)
- 16233 -> https://jobs.sana.de/de/sites/CX_4025/ read via hcmRestApi/.../findReqs;siteNumber=CX_4025 on fa-eycl-saasfaeuraprod1.fa.ocs.oraclecloud.com (jobs.sana.de 302s these to 404)
- 56201 -> https://jobs.malteser.de/de/job-offer-list/ (vacancies are off-host from waldkrankenhaus.de)
- 18402 -> https://recruitingapp-5656.de.umantis.com/Jobs/1?CompanyID=22&Reset=G (replaces the kbo.de group-CMS walk)
- 18712 -> https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobSite%3Akbo-Inn-Salzach-Klinikum+Wasserburg+am+Inn (current value has zero job links; today's 0 is right by luck)
- 16107 -> https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobLocation%3Akbo-Donau-Altm%C3%BChl-Kliniken (carry the facet value as the row's city)
- 17704 -> https://kbo.de/karriere/jobs (+ read the jobSite facet per job)
- 27501 -> https://karriere.ge-passau.de/stellen/
- 77901 -> https://dongku.de/stellenangebote/ (current value is a now-delisted single job-detail page)
- 76301 -> keep https://karriere.klinikverbund-allgaeu.de/ but set ats_type='umantis' (its 3 siblings already have it)
- 47601 -> NO working replacement found; current value is a single job-detail UUID page and the board is Akamai-walled

One corrupted row found, worth a table-wide scan: clinic 47503 has name = 'Bezirksklinik Rehau Rehau Taeger KU Gesundheitseinrichtungen des Bezirks Oberfranken' and town = '(GeBO)' -- a whole CSV line mashed into the name field. It can neither match nor be matched against and is a plausible contributor to the GeBO misrouting.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Each listed clinic_id carries the corrected careers_url/ats_type in the live clinics table, applied through the sanctioned EdgeSink clinics op (no direct PostgREST writes)
- [x] #2 Clinic 47503's mashed name/town row is repaired, and the whole table is scanned for the same shape (town containing punctuation, name over a sane length); report what else was found
- [x] #3 A registry lint rejects a careers_url matching a known job-detail-page shape (/job/<uuid>/, /stellenangebote/<slug>/<slug>/) -- it would have caught 47601 and 77901 before either produced junk rows
- [ ] #4 After applying, re-crawl the affected clinics and report postings recovered per clinic against the audit's expected numbers
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Re-verify each of the ~20 proposed careers_url/ats_type corrections live (HTTP + Playwright render where needed) before trusting the audit -- compare against both CSV and the live pflege_jobs.clinics table (they disagree per TASK-80).
2. Apply corrections directly to data/registry/clinics.csv (this task's own file, not production). Build the live-table side as a DRY-RUN EdgeSink.write_clinics() payload only -- no production writes this round.
3. Fix clinic 47503's mashed name/town row (verify the real facility against the operator's own site), then scan the whole 407-row table for the same shape and report everything found.
4. Implement pflege_jobs/registry_lint.py (+ tests/test_registry_lint.py): reject a careers_url matching /job/<uuid>/ or /stellenangebote/<slug>/<slug>/. Mutation-test via a /tmp copy.
5. Run targeted tests, then the full offline suite once. Write dry-run reports under backups/. Update backlog with honest per-AC evidence.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Re-verified all ~20 proposed URLs live (curl/requests, Playwright render for 2 JS-rendered boards) against BOTH data/registry/clinics.csv and the live pflege_jobs.clinics table -- they frequently disagreed (TASK-80's finding, confirmed concretely). Full evidence and per-clinic rationale: backups/task86-registry-dryrun-2026-09-21.md and the machine-readable backups/task86-registry-dryrun-2026-09-21.json (exact EdgeSink.write_clinics payloads, unexecuted).

CSV (data/registry/clinics.csv, owned by this task): 20 rows corrected directly -- 47503 (name/town/operator) plus 19 careers_url/ats_type corrections (27106, 16201, 16203, 16215, 56404, 76201, 16214, 36202, 66301, 57408, 37202, 16233, 18402, 17704, 18712, 16107, 27501, 77901, 76301). 66101/77406/76203/76114 needed NO csv edit -- csv already held the correct value; only live is stale for those. 56201 and 47601 NOT corrected anywhere: 56201's proposed URL (jobs.malteser.de/de/job-offer-list/) verified DEAD (HTTP 404) today -- exactly the "audit input was stale" risk the brief warned about; 47601 confirmed Akamai-walled (403), no replacement exists, matches crawlers/routing.py's own WALLED pattern.

Live pflege_jobs.clinics table: NOT mutated (explicit instruction this round). 22 dry-run corrections staged as full EdgeSink.write_clinics() payloads (every CLINIC_SPEC column, only the confirmed field(s) changed, ats_type left blank/coalesced where no target value could be confirmed -- guessing an ATS type would be exactly the "wrong correction worse than none" failure mode). Confidence noted per row: 9 HIGH, 8 MEDIUM/MEDIUM-HIGH with a named reason (mostly Oracle-HCM SPA shells that plain HTTP can't fully verify, or a shared board with a live TASK-81 attribution conflict), 1 LOW-MEDIUM (16233/Sana, task's own text says the real read path 302s to 404).

47503 corrupted row: verified the real facility against the operator's own site (gebo-med.de, path /standorte/bezirksklinik-rehau) -- name->"Bezirksklinik Rehau", town->"Rehau", operator->"KU Gesundheitseinrichtungen des Bezirks Oberfranken (GeBO)" (was empty). Fixed directly in the CSV. Its careers_url was already correct live (gebo-med.de/karriere, softgarden) from an unrelated earlier process; CSV still lacks it -- that's the TASK-80 CSV-sync gap, not this task's file scope to invent a value for.

Whole-table scan for the same shape found 28 MORE rows (29 total, not 1) -- the table's own parse_quality=partial column already flags exactly this class (29/407 rows) and matches an independent town/name-shape heuristic. 27 of the 28 are duplicate registrations of a hospital already present cleanly elsewhere under a different clinic_id (status=Vertrags-KH duplicate of a Plan-KH twin) -- found clean twins for 20 of them by evidence (name/town/operator overlap), 7 have no confident twin and would need the same kind of external check 47503 got. 3 of the 28 (57570, 77672, 78071) carry a real, different careers_url their own clean twin lacks -- a naive "delete the corrupted duplicates" cleanup would silently lose that data. None of the 28 fixed (AC#2 only asks for 47503 to be repaired and the rest reported) -- full table in the dry-run report.

Bonus finding via the new lint: the identical dead single-job-detail URL on 47601 is ALSO on clinics 67201 and 67601 (both HELIOS, both Akamai-walled) -- not in the original ~20-item audit list, found by running registry_lint over the whole post-correction table.

pflege_jobs/registry_lint.py + tests/test_registry_lint.py: 7 tests, all green. Mutation-tested (copied to /tmp, gutted check_careers_url to always return None, 4/7 tests went red as expected -- the other 3 are negative-space assertions that should stay green when disabled -- restored from the /tmp copy, all 7 green again; never used git for this).

Did NOT touch pflege_jobs/registry.py (TASK-80/attribution agent's file) or crawlers/vendor_adapters.py / career_crawl.py (TASK-85/zero-yield agent's files) -- flagged coordination needs with both in the dry-run report instead (shared bezirkskliniken-schwaben.de board ambiguity, kbo 18402/17704 shared-board attribution, 56201 Malteser adapter).

Full offline suite: see next note / final summary for the run captured at the end of this session.

Correction to the confidence count above: 22 live dry-run corrections break down as 9 HIGH, 5 MEDIUM-HIGH, 7 MEDIUM, 1 LOW-MEDIUM (12 total MEDIUM+MEDIUM-HIGH, not 8 as first written).

Full offline suite (pytest -m "not network", matching the brief's own "full OFFLINE suite" phrase and pytest.ini's network marker): 3 failed, 1288 passed, 1 skipped, 1197 deselected, 419s. Took 3 attempts -- first two (bare `pytest -q`, no marker) were each killed by their own 600s/900s timeout wrapper before finishing; `ps aux` during the second attempt showed a SECOND, independent full-suite pytest run already in flight from the parallel attribution agent (command line referenced /tmp/task81_mut/full_suite.out) -- confirms genuine shared-VM contention from sibling agents in this same round, not a hang in this task's change.

All 3 failures are NOT this task's: two are in tests/test_inherited_fields.py (test_matcher_refuses_a_clinic_matched_by_its_own_inherited_name, test_employer_inherited_from_real_pipeline_suppresses_r1_r2_on_a_shared_board), both `from pflege_jobs.registry import Matcher` and both fail with `TypeError: 'NoneType' object is not subscriptable` inside Matcher.match() -- confirmed by re-running the first in isolation. pflege_jobs/registry.py is currently modified (git status) by the parallel TASK-80/attribution agent, who owns that file; this task never touched it. Third is tests/test_web_clawl.py::test_cancel_is_two_click_and_hits_the_cancel_endpoint, a Playwright UI test asserting a "cancel requested" label appears within a fixed 400ms wait -- unrelated to registry data, consistent with a timing flake under the same VM contention.

Baseline was 1266 passed/1 skipped/0 failed (1267 total); this run's non-failing total is 1288+1=1289 tests plus these 3 = 1292, i.e. 25 more tests exist now than the baseline count -- expected, other parallel units in this same round are adding tests concurrently.

This task's OWN tests (tests/test_registry_lint.py, 7 tests) passed cleanly in every one of the 3 full-suite attempts and in isolation, and were mutation-tested earlier (see above). No test in any file this task owns or touched failed.

2026-09-22 follow-up round. Previous round's 3 owned files were sha256-identical to already-approved commit a5c01d6 (confirmed via sha256sum before touching anything: registry_lint.py 3456d579b003, test_registry_lint.py 78eaa63114e6, clinics.csv 11fff4abda79 -- matched exactly). This round delivers real diffs against all 7 reviewer findings.

(#2, wiring) EdgeSink.write_clinics (pflege_jobs/sinks.py:173) is the one funnel career_discover_exa.py's Exa write-back and cli.py's ats-probe drain both post through (grep for write_clinics repo-wide, excluding tests, shows exactly these 2 real callers plus an unrelated mock class in tools/task95_replay.py). It now runs registry_lint.lint_rows() before posting and raises ValueError -- no partial-batch write, no silent skip -- if any row matches a job-detail shape. This is the one call site outside the literal owned-file list, as the brief permitted.

(#5, live regression) Verified live 2026-09-22: rotkreuzklinikum-muenchen.de/stellenangebote/ (this task's own prior CSV value for 16215) loads 200 but calls concludis('setMultiJobBoard', '6|18|24|30'); crawlers/vendor_adapters.py's CONCLUDIS_BOARD regex matches only setJobBoard, so concludis_widget() returns (None, None) and crawl_wp_jobs returns 0 rows for it (measured directly, live). The URL TASK-77 already made work, https://www.schwesternschaft-muenchen.de/stellenangebote/index.php, returns 17 rows today via the same crawl_wp_jobs call (also measured live) and IS what live currently, correctly, still serves -- it was never mutated. data/registry/clinics.csv reverted 16215 to that URL. Did not touch crawlers/vendor_adapters.py (sibling-owned this round, in git status as modified) -- the setMultiJobBoard regex gap is unfixed and belongs there.

(#3 + #7, live sweep + widened pattern) Ran lint_rows() over all 407 live pflege_jobs.clinics rows (PostgREST, Accept-Profile: pflege_jobs): 6 hits, not the 3 previously reported (that section had only ever run against the CSV) -- 47601/67201/67601 (job-slug) and 77901/77902/77903 (stellenangebote-slug-slug; live has not received the CSV's 77901 fix yet). Measured concretely: clinic 77902 carries 7 open live postings, all sourced from the shared dead job-detail URL; 77901 carries 2 (a /schulleben/ news page and a .pdf, neither a real posting). Widened registry_lint.py's job-uuid pattern (UUID-only) to job-slug (any /job/<slug>/) -- the old pattern missed 16268 and 47102, both real live /job/<slug>/ single-posting URLs verified today. CSV lint count after the 16215 fix: 5 (16268, 47102, 47601, 67201, 67601) -- none of these 5 has a verified replacement board this round, correctly left flagged, not guessed.

(#4, fragile test) Replaced test_lint_csv_catches_the_still_uncorrected_47601_row (asserted a specific clinic_id is still broken in the real data/registry/clinics.csv -- true only because that row hadn't been fixed yet, would go red the moment someone fixes it) with test_lint_csv_reads_the_clinics_csv_dialect_and_flags_job_detail_rows(tmp_path): a synthetic-fixture test of lint_csv()'s own CSV-reading behavior, independent of repo data state.

(#6, doc bug) The dry-run JSON already staged both careers_url and ats_type for 76301; only the .md's prose table and bucket list disagreed with its own JSON. Fixed both spots.

Also corrected backups/task86-registry-dryrun-2026-09-21.json itself for the 16215 regression (removed the bad live-push entry, added a not_applied entry explaining why) -- outside the literal owned-file list, but it is exclusively a task-86 artifact no sibling task touches, and leaving it staged wrong next to a corrected .md would be misleading. Flagging this explicitly as a judgment call.

Mutation-tested 2026-09-22 via /tmp copies, restored after, never used git: (a) gutted check_careers_url to always return None -- 6/10 tests in tests/test_registry_lint.py went red, the other 4 are negative-space (assert ... is None) and correctly stayed green. (b) separately removed only the write_clinics gate (lint logic untouched) -- exactly 1 test went red, the new wiring test, and the other 23 (including all of test_sinks.py's pre-existing write_clinics coverage) stayed green. Both mutations restored; tests/test_registry_lint.py + tests/test_sinks.py -> 24 passed both times after restore.

Targeted tests green throughout: test_registry_lint.py, test_sinks.py, test_ats_seeds.py, test_completeness_js_widget_boards.py, test_career_discover_exa.py = 55 passed, 0 failed.

Full offline suite, run once (.venv/bin/python -m pytest -m "not network"): 1341 passed, 1 skipped, 1197 deselected, 0 failed, 418.85s. Baseline at 8bf6d63 was 1336 passed/1 skipped/0 failed -- the +5 reflects concurrent sibling work in the same shared working tree (this task's own files added 3 net new tests, 7->10 in test_registry_lint.py).

2026-09-22, third round (fixing the 3 concrete problems an independent review found in round 2's own fix). Files touched: pflege_jobs/sinks.py, pflege_jobs/cli.py (cmd_link_clinics only), tests/test_registry_lint.py, backups/task86-registry-dryrun-2026-09-21.md. data/registry/clinics.csv and pflege_jobs/registry_lint.py confirmed UNCHANGED this round (diffed against session-start snapshots before finishing) -- none of the 3 findings needed changes there.

Problem #1 (sinks.py:184, gate rejects a carry-through row and aborts the whole batch): root cause was blast radius, not the check itself. write_clinics now checks each row's careers_url independently and, if job-detail-shaped, sends "" for JUST that field instead of raising. Verified independently against the deployed edge function source (edge/pflege-ingest/index.template.ts:76): `careers_url=coalesce(nullif(excluded.careers_url,''), pflege_jobs.clinics.careers_url)` -- sending "" is a true no-op, confirmed against the real upsert SQL, not only sinks.py's own comment. Every other column in that row, and every other row in the batch, is still written; no ValueError path remains in write_clinics. Reproduced the bug first (200 clean rows + 1 carried-over 16268 row -> ValueError, 0/201 written, confirmed by running the new test against the pre-fix code before editing it), then fixed it (same scenario -> 201/201 written, only the 1 bad value scrubbed).

Problem #2 (cli.py:138, cmd_link_clinics bypasses write_clinics via sink._post directly): cmd_link_clinics now calls sink.write_clinics(...) instead of manually chunking and posting. This is a second call site beyond the original brief's "ONE call site" framing -- justified because this round's reviewer named it explicitly as unresolved ("the one that matters most" from the prior round's brief). Confirmed data/registry/clinics.csv still holds 5 lint-flagged rows (16268, 47102, 47601, 67201, 67601) this command would otherwise push to production ungated.

Problem #3 (tests/test_registry_lint.py:98, no mixed-batch/carry-over pin): added test_write_clinics_scrubs_only_the_bad_careers_url_not_the_whole_batch (200 clean + 1 carried-over-bad row; asserts all 201 written, only the bad value scrubbed) and test_cmd_link_clinics_routes_the_csv_push_through_the_same_lint (drives the real cmd_link_clinics with a lint-flagged CSV row through mocked HTTP, asserts the posted careers_url is blanked). Rewrote test_write_clinics_rejects_a_job_detail_careers_url_before_posting for the new scrub behavior (was pytest.raises(ValueError,...), now asserts the row is posted with careers_url=""). tests/test_registry_lint.py: 10 -> 12 tests.

Evidence (all measured this session):
- All 3 new/rewritten tests reproduced red against the pre-fix code (ran before editing sinks.py/cli.py), green after.
- Mutation-tested via /tmp copies (never git): (a) gutted check_careers_url -> None always: 8/12 red, 4/12 correctly-insensitive green (negative-space assertions). (b) disabled just the write_clinics scrub loop, lint logic left intact: exactly 3/12 red -- both write_clinics wiring tests AND test_cmd_link_clinics_routes_the_csv_push_through_the_same_lint, proving cmd_link_clinics's fix genuinely depends on the same gate, not a copy of it. Both mutations restored from /tmp copies, diff-confirmed byte-identical afterward, re-ran green (62 passed across test_registry_lint.py + test_sinks.py + test_cli_inbox_probe.py + test_career_discover_exa.py + test_inbox_sqlite_queue.py, both before mutating and after restoring).
- Fresh live lint sweep this session (PostgREST, all 407 pflege_jobs.clinics rows, Accept-Profile: pflege_jobs): 8 hits, not 6 as this doc's Sec5 previously said -- that earlier sweep excluded 16268/47102 from the LIVE check (checked only against the CSV). Corrected in backups/task86-registry-dryrun-2026-09-21.md (Sec5 + new item 8 in the correction-pass list).
- Fresh CSV lint sweep this session: unchanged at 5 hits (16268, 47102, 47601, 67201, 67601).
- Re-measured 77901/77902/77903 open-posting counts live this session (not copied from the prior pass, which said 77902=7/77901=2): 77902=7 (matches), 77901=3 today not 2 (a third junk row, /tag/stellenangebot/, appeared since the prior pass -- real crawl movement, flagged explicitly as a discrepancy rather than silently reused), 77903=0. Recorded in the .md.

No production data was written or mutated this round -- all verification was read-only GETs (PostgREST) or fully offline (pytest, /tmp mutation copies).

Targeted tests: tests/test_registry_lint.py tests/test_sinks.py tests/test_cli_inbox_probe.py tests/test_career_discover_exa.py tests/test_inbox_sqlite_queue.py tests/test_ats_seeds.py -> 66 passed, 0 failed.

Full offline suite, run once (.venv/bin/python -m pytest -m "not network"): 1352 passed, 1 skipped, 1197 deselected, 0 failed, 404.96s. (First attempt this session died with no summary line, process vanished around ~93% progress with zero visible failures up to that point -- confirmed via `ps aux` showing 3+ concurrent sibling pytest processes at the time, one running since 04:53/90+ min, and ListAgents showing 54 concurrent peer sessions on this VM; retried once more, per the same VM-contention pattern earlier rounds documented, and it completed clean.) Baseline at 8bf6d63: 1336/1/0. Prior round (first correction pass, same day): 1341/1/0. This round: +11, consistent with continued concurrent sibling work landing in the same shared tree (this round's own tests account for +2 net of the 11).

DISCOVERED BUT NOT FIXED (outside this round's 3 named problems and outside this task's owned files -- reporting per "no silent scope expansion" rather than either fixing a sibling-owned file or staying quiet): app/crawl.py:969 (inside refetch_career(), `EdgeSink()._post({"clinics": [row]})`) is a FOURTH real production funnel for clinics.careers_url that posts directly, bypassing write_clinics and this lint entirely. Like career_discover_exa.py it only ever writes a NEW careers_url when the stored one was blank, but it can still carry through an already-bad, unchanged live value when only ats_type changes -- the same shape as problem #1, in a caller my fix doesn't reach. app/crawl.py is TASK-95's owned file this round (per TASK-95's own backlog notes); not touched. Flagging for a human decision: either push the scrub down into EdgeSink._post keyed on body.get("clinics") (containable entirely within sinks.py, would catch every current and future clinics-payload caller including this one, but broadens a low-level, payload-agnostic method's responsibility), or have TASK-95 route this call through write_clinics directly. Lower urgency: data/sync_krankenhausplan_2026.py:130 posts a clinics payload via raw requests.post, not through EdgeSink at all -- but it's a one-off annual Krankenhausplan-merge script, not the recurring pipeline, and its careers_url values are carried from the existing registry, not freshly introduced.

AC#3's evidence is materially stronger after this round: the previous round's wiring, if it had ever run against a real mixed batch (which the review showed both real callers produce), would have wedged cli.py's inbox drain permanently (raise before ack_fn, re-raising on every retry) -- a production-breaking defect in AC#3's own enforcement mechanism, not merely "unwired." That defect is fixed and covered by a red-before/green-after test plus two independent mutation passes.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-22 05:56
---
Scope note for reviewer: this round's owned-file list named backups/task86-registry-dryrun-2026-09-21.md but not its .json companion. I corrected the .json too (removed the 16215 live-push entry that would have caused a regression if applied, added a not_applied entry explaining why) because it is exclusively a task-86 artifact -- no sibling task references or edits it -- and leaving it staged wrong right next to a .md that now says "do not push this" would be a landmine for whoever applies the dry-run plan next. Flagging explicitly in case this should have been left for a human to fix by hand instead.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
2026-09-22 follow-up: the prior round delivered nothing (all 3 owned files sha256-identical to approved commit a5c01d6, confirmed before starting). This round fixes all 7 reviewer findings with real, verified diffs -- see git diff on pflege_jobs/registry_lint.py, pflege_jobs/sinks.py, tests/test_registry_lint.py, data/registry/clinics.csv, and the updated backups/task86-registry-dryrun-2026-09-21.md/.json.

Wired the lint into EdgeSink.write_clinics (pflege_jobs/sinks.py), the one funnel career_discover_exa.py and cli.py's ats-probe both post careers_url through -- it now raises ValueError and refuses the whole batch if any row is job-detail-shaped (mutation-tested: removing the gate turns exactly 1 test red, the rest of test_sinks.py's coverage is unaffected).

Fixed a live regression in clinic 16215 (Rotkreuzklinikum Nymphenburger Str.): this task's own prior CSV correction pointed it at a URL that returns 0 rows today because crawlers/vendor_adapters.py's concludis regex matches only setJobBoard, not this page's setMultiJobBoard (verified live). Reverted the CSV to the URL TASK-77 already made work (17 rows live today, confirmed live, never mutated). Did not touch crawlers/vendor_adapters.py (sibling-owned this round).

Widened the lint's job-uuid pattern to job-slug (any /job/<slug>/, not only a UUID) -- the narrow pattern missed 2 real live single-job-detail URLs (16268, 47102). Ran the lint against all 407 live pflege_jobs.clinics rows, not only the CSV: 6 hits, not the 3 previously reported -- 47601/67201/67601 plus 77901/77902/77903 (measured: clinic 77902 carries 7 live postings and 77901 carries 2 junk rows, all sourced from the same delisted URL still live in production). Replaced the test that asserted the 47601 row is still broken in production data (would go red the moment someone fixes it) with a synthetic-fixture test of lint_csv()'s own behavior. Fixed a report/JSON disagreement over clinic 76301's careers_url+ats_type bucketing, and corrected the dry-run JSON's own staged (wrong) live push for 16215.

Mutation-tested via /tmp copies, restored after, never git: check_careers_url gutted -> 6/10 tests red, 4 correctly-insensitive negative-space tests stayed green; write_clinics gate removed -> exactly 1 (the wiring) test red, 23 stayed green. Both restored, all green after.

Full offline suite, run once (.venv/bin/python -m pytest -m "not network"): 1341 passed, 1 skipped, 1197 deselected, 0 failed, 418.85s (baseline 1336/1/0 at commit 8bf6d63; delta includes concurrent sibling work landing in the same shared tree this round).

AC#1 and AC#4 remain unchecked: no production DB write happened or was permitted this round (explicit instruction). Both need a human to run the now-corrected dry-run payload in backups/task86-registry-dryrun-2026-09-21.json via EdgeSink.write_clinics(), then re-crawl the affected clinics and report recovered postings. AC#2 and AC#3 stay checked from the prior round; AC#3's evidence is now materially stronger (wired into the write path and swept against live, not only unit-tested against the CSV in isolation).

2026-09-22, third round: fixed all 3 concrete problems an independent review found in round 2's registry-lint wiring, re-ran the full suite, corrected the backlog/dry-run numbers the review disproved.

#1 (sinks.py:184, whole-batch abort on a carry-through value): write_clinics now scrubs only the offending careers_url (sends "" so the edge upsert's coalesce keeps whatever is already stored, verified against the actual deployed SQL in edge/pflege-ingest/index.template.ts) instead of raising for the entire batch. Every other column/row still gets written.
#2 (cli.py:138, cmd_link_clinics bypasses the lint): now routed through sink.write_clinics(...) instead of sink._post directly, closing the second real production funnel (the CSV still carries 5 lint-flagged rows this command would otherwise push ungated).
#3 (tests/test_registry_lint.py:98, no mixed-batch pin): added a 200-clean+1-carried-over-bad-row test and an end-to-end cmd_link_clinics wiring test; rewrote the old raise-based test for the new scrub behavior. 10 -> 12 tests.

Evidence: all 3 new/rewritten tests reproduced red against the pre-fix code, green after. Two independent /tmp-copy mutation passes (gut the lint logic; disable just the wiring) each killed exactly the tests they should have and nothing else, restored and re-confirmed green both times. Targeted set: 66 passed. Full offline suite, run once: 1352 passed, 1 skipped, 1197 deselected, 0 failed, 404.96s (baseline 1336/1/0; prior round 1341/1/0) -- first attempt this session died with no summary under heavy shared-VM contention (54 concurrent peer sessions via ListAgents), retried once and completed clean.

Corrected backups/task86-registry-dryrun-2026-09-21.md: the live lint sweep it reported (6 hits) excluded 16268/47102 from the live check; a fresh sweep this session finds 8 (matches the reviewer's independent number). CSV sweep unchanged at 5. Re-measured 77901/77902/77903 open-posting counts live (77901 now 3, not the prior pass's 2 -- real crawl movement, flagged not silently reused).

data/registry/clinics.csv and pflege_jobs/registry_lint.py are unchanged this round (confirmed by diff) -- neither of the 3 findings needed changes there.

Discovered, NOT fixed (outside this round's 3 named problems and outside this task's owned files): app/crawl.py:969 (refetch_career(), TASK-95-owned) posts a clinics payload via EdgeSink()._post directly, bypassing write_clinics and this lint -- a fourth real funnel that can carry through an already-bad live careers_url the same way problem #1 did. Flagged for a human/TASK-95 decision (route it through write_clinics, or push the scrub down into EdgeSink._post itself). Also lower-urgency: data/sync_krankenhausplan_2026.py's one-off annual sync posts via raw requests, not EdgeSink at all.

AC#3 stays checked -- its evidence is now materially stronger: the previous wiring, exercised against a real mixed batch (which both real callers routinely produce), would have permanently wedged cli.py's inbox drain (raise before ack_fn, re-raising every retry) -- a production-breaking defect in AC#3's own enforcement, now fixed and covered by red/green tests plus two mutation passes. AC#1 and AC#4 remain unchecked: no production DB write happened or was permitted this round. AC#2 unaffected, stays checked.
<!-- SECTION:FINAL_SUMMARY:END -->
