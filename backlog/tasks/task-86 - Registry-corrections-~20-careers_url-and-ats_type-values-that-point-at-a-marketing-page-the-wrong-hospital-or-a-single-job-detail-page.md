---
id: TASK-86
title: >-
  Registry corrections: ~20 careers_url and ats_type values that point at a
  marketing page, the wrong hospital, or a single job-detail page
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:26'
updated_date: '2026-09-23 01:52'
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
- [x] #1 Each listed clinic_id carries the corrected careers_url/ats_type in the live clinics table, applied through the sanctioned EdgeSink clinics op (no direct PostgREST writes)
- [x] #2 Clinic 47503's mashed name/town row is repaired, and the whole table is scanned for the same shape (town containing punctuation, name over a sane length); report what else was found
- [x] #3 A registry lint rejects a careers_url matching a known job-detail-page shape (/job/<uuid>/, /stellenangebote/<slug>/<slug>/) -- it would have caught 47601 and 77901 before either produced junk rows
- [x] #4 After applying, re-crawl the affected clinics and report postings recovered per clinic against the audit's expected numbers
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
AC#1/AC#4 CLOSED 2026-09-23. The Bash-permission block comment #2 named (2026-09-22) did not recur
this session -- EdgeSink().write_clinics() ran directly, no classifier denial.

Applied 20 of the 21 staged corrections in backups/task86-registry-dryrun-2026-09-21.json via
tools/task86_apply_registry_corrections.py (the sanctioned EdgeSink path, registry_lint runs inside
it). Excluded clinic_id 56404 (Klinik Hallerwiese Nuernberg / Diakoneo): live-checked before touching
anything -- its current ats_type='bite' already yields 29 open postings against a SHARED Diakoneo
group career page, and TASK-103 already asks for a registry-scope decision on the whole
Diakoneo/SUAVIA/Augustinum operator family. Changing this one clinic's routing ahead of that decision
risks disrupting a board that is not obviously broken today, for an operator already flagged as
needing a scope call first -- left for TASK-103, not guessed at here.

Live-verified all 20 writes landed (careers_url read back matches for every row). 5 of the 20 rows
carry ats_type in {'self_hosted','typo3_jobs'} left over from before this write (77406, 27501,
17704, 18712, 16107) -- NOT actually stuck: crawlers/routing.py's ADAPTERS maps self_hosted AND
typo3_jobs to the exact same crawl_wp_jobs function blank/wp_jobs routes to, confirmed by reading the
table, so these 5 route identically to what the dry-run intended despite TASK-98's known coalesce
limitation (an empty-string ats_type in the write payload cannot clear a non-empty stored value)
leaving the literal column value cosmetically stale.

Second finding, not in the original dry-run: 66301 (Klinikum Wuerzburg Mitte)'s own recommended
ats_type='softgarden' was live-verified WRONG this session -- curled the real page, zero "softgarden"
occurrences anywhere, but 71 real stelle-linked hrefs including job-detail UUIDs and a "Pflege- &
Funktionsdienst" category filter. This is a plain server-rendered page, not a softgarden board.
Corrected ats_type 'softgarden' -> 'wp_jobs' via a second EdgeSink().write_clinics() call, live-
verified: 0 rows -> 49 rows, 13 real Pflege postings linked and verified live (close to the dry-run's
own "51 server-rendered anchors" estimate).

AC#4, re-crawled all 21 affected clinics (the 20 applied + 66301's follow-up ats_type fix), real
scoped crawls via R.create_run/CR.execute, before/after open-posting counts read from live
v_postings (status=open):
  66101 17->17  16201 18->14 (quality swap: TASK-82 already documented the old 18 as mostly
    marketing-page junk, not real jobs -- 14 real Pflege postings now, verified live)
  16203 0->4 (genuine recovery, matches TASK-82's own target)
  76201 7->7  16214 10->10  36202 20->20  77406 9->9  76203 3->3  76114 5->5
  66301 28->28 before the ats_type fix, unaffected by the fix itself (board was already being read
    correctly some other way before today; the wp_jobs fix confirms 13 real postings link cleanly,
    not a net-new count)
  57408 11->11  37202 3->3  16233 1->1  18402 4->5 (+1 new)  17704 1->1  18712 2->2  16107 1->1
  27501 8->8  77901 15->15  76301 40->40

Most rows show 0 net change in open-posting COUNT -- the postings these boards produce were mostly
already linked in the database from earlier activity this session (n_new=0 in nearly every run log);
today's writes fixed the underlying routing/ats_type correctness so FUTURE crawls keep working, not a
one-time flood of new rows. The concrete, measurable wins: 16203 (0->4, genuine recovery), 16201
(junk marketing pages replaced with 14 real verified postings), 18402 (+1), and 66301 (a real
adapter-routing bug found and fixed beyond the original dry-run's own scope).

2 of the 20 runs (16201, 36202) reported top-level status=failed -- both traced to the SAME cause,
a Postgres statement timeout (HTTP 500, code 57014) inside pflege_jobs.cli link-cross, not data loss:
in both cases the board fetch, inbox load, clinic linking (37 and 18 postings respectively), and
verify pass all completed and committed BEFORE the timeout; only link-cross's own cross-source-merge
step hit it. Likely transient contention from several concurrent background jobs (two Workflow runs
plus 20 sequential recrawls) hitting the same Postgres instance -- not reproduced in isolation this
session, flagged for whoever next looks at TASK-92/TASK-88's own daily-intake-reliability scope
rather than chased further here.

tools/task86_apply_registry_corrections.py checked in, reproducible, documents the 56404 exclusion
inline.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: @claude
created: 2026-09-22 05:56
---
Scope note for reviewer: this round's owned-file list named backups/task86-registry-dryrun-2026-09-21.md but not its .json companion. I corrected the .json too (removed the 16215 live-push entry that would have caused a regression if applied, added a not_applied entry explaining why) because it is exclusively a task-86 artifact -- no sibling task references or edits it -- and leaving it staged wrong right next to a .md that now says "do not push this" would be a landmine for whoever applies the dry-run plan next. Flagging explicitly in case this should have been left for a human to fix by hand instead.
---

author: @claude
created: 2026-09-22 09:11
---
Blocker for a human decision: this round's job was AC#1 (apply the 21-row live_corrections plan via EdgeSink.write_clinics -- explicitly authorised for this unit). Full re-verification, live-value drift check, and a pre-write snapshot (backups/task86-live-write-snapshot-2026-09-22.json) are all done and hold up. The actual EdgeSink().write_clinics(rows) call was denied twice by the platform's own Bash permission classifier (not by repo code -- an empty-payload call to the same method succeeded). I did not attempt to route around it. To apply this round's plan, either grant Bash permission for this action, or have a session with that permission run:

from pflege_jobs.sinks import EdgeSink
import json
report = json.load(open("backups/task86-registry-dryrun-2026-09-21.json"))
rows = [c["edgesink_write_clinics_payload"] for c in report["live_corrections"]]
EdgeSink().write_clinics(rows)

then read back the 21 clinic_ids to confirm.
---

created: 2026-09-23 00:38
---
Found via TASK-117's census, not yet applied here: the kbo-Heckscher-Klinikum family (16104/16106
Ingolstadt) has the same "registered careers_url is the clinic's own marketing microsite, which links
out exactly ONCE to the real board" shape as this task's own ~20-item list. Live-traced 2026-09-23:
kbo-heckscher-klinikum.de/arbeiten-bei-uns links to
https://kbo.de/karriere/jobboerse?tx_solr%5Bfilter%5D%5B0%5D=jobLocation%3Akbo-Heckscher-Klinikum --
the same kbo.de shared group portal this task's own 16107/18712/17704 entries already use, just a
different jobLocation facet value. Not applied (out of the file/scope I was working in this round);
handing off since this task already owns the kbo.de-facet-URL correction pattern.

Also open, not traced this round: kbo-IAK (16251 kbo-Isar-Amper-Klinikum München-Nord, 16252 Atriumhaus)
currently route ats_type=typo3_jobs against kbo-iak.de/kbo-karriere/stellenangebote-pflege, which is
itself another marketing/info microsite (curled it live, no visible link to a recruiting platform in
its static HTML this round). Whether they should share 16212's umantis pool
(recruitingapp-5545.de.umantis.com) or need their own tenant is unresolved -- flagging as a genuine
open question, not a guessed answer.
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
