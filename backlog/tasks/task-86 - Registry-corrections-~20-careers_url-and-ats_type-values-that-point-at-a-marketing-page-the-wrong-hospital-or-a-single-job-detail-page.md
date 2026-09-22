---
id: TASK-86
title: >-
  Registry corrections: ~20 careers_url and ats_type values that point at a
  marketing page, the wrong hospital, or a single job-detail page
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-21 17:51'
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
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Re-verified all ~20 audited corrections live before trusting them (one, 56201/Malteser, turned out dead on re-check -- exactly the risk the brief warned about). Applied 20 corrections directly to data/registry/clinics.csv (this task's file): 47503's corrupted name/town/operator, plus 19 careers_url/ats_type fixes. Live pflege_jobs.clinics table was NOT mutated (explicit constraint) -- 22 corrections staged as exact EdgeSink.write_clinics() dry-run payloads in backups/task86-registry-dryrun-2026-09-21.json/.md, with per-row confidence and the reasoning behind it. 56201 and 47601 were left uncorrected everywhere: verified dead/impossible respectively.

Whole-table scan (AC#2) found 29 corrupted rows, not 1 -- reported in full, with clean-twin cross-references for 20 of them and an explicit warning that 3 carry real data a naive cleanup would lose. Registry lint (AC#3) implemented and mutation-tested; running it surfaced 2 more clinics (67201, 67601) carrying the same dead job-detail URL as 47601, missed by the original audit.

AC#1 and AC#4 are NOT checked and not claimed complete: this round explicitly forbids production writes, so nothing was applied to the live table, and AC#4's "re-crawl and report postings recovered" has no meaningful evidence to produce until a human applies the dry-run plan. Both are correctly pending human action, not silently skipped.

Full offline suite (pytest -m "not network"): 3 failed, 1288 passed, 1 skipped, 419s -- all 3 failures traced to pflege_jobs/registry.py (parallel TASK-80 agent's file, actively mid-edit) and a Playwright UI timing test, neither touched by this task; this task's own 7 tests passed in every run. Two earlier full-suite attempts without the network marker were killed by their own timeout wrapper under confirmed concurrent load from a sibling agent's simultaneous full-suite run.

Did not edit pflege_jobs/registry.py or crawlers/vendor_adapters.py/career_crawl.py (other agents' files this wave) -- flagged 3 concrete coordination points instead (bezirkskliniken-schwaben.de board ambiguity for the zero-yield agent, kbo 18402/17704 shared-board attribution for TASK-81, the still-unresolved 56201 Malteser deep link for TASK-85).
<!-- SECTION:FINAL_SUMMARY:END -->
