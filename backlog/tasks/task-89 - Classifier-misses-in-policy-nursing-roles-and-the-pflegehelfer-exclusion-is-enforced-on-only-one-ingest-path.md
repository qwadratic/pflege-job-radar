---
id: TASK-89
title: >-
  Classifier misses in-policy nursing roles, and the pflegehelfer exclusion is
  enforced on only one ingest path
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-21 04:27'
updated_date: '2026-09-22 01:41'
labels: []
dependencies: []
ordinal: 89000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Top-100 coverage audit 2026-09-21, findings M8 and M9.

Classifier misses (about 7 postings, plus precision):
- Hygienefachkraft is the recurring one: classify_role returns ('apn_experte','apn_experte:hygienefachkraft'), which is IN policy, yet 4 such rows across 36201, 76401 (x2) and 76301 are absent from the database although the adapter returns them. The drop happens AFTER classify and is unexplained -- tracing it is the valuable part of this task, because an in-policy row disappearing between adapter and database implicates the intake path, not the classifier.
- 'OP Leitung (m/w/d)' -> ('nicht_pflege','no_pflege_token'): the leitung rule requires a pflege token (18001).
- 'Onkologische Fachkraft (w/m/d)' -> ('nicht_pflege','no_pflege_token') despite section_labels carrying workarea 'Pflege- und Funktionsdienst' (18811, pflege_jobs/classify.py:90).
- patterns.json:77 'medizinische/?r? fachangestellte' does not match the inflected 'Medizinischen Fachangestellten', so MFA rows leak in (47701).

Policy applied inconsistently (about 12 rows, a decision rather than a bug): pflegehelfer is in patterns.json:347 excluded_role_classes and correctly drops 10+ rows (Sana Hof, Helios München West x2, GAP x3, InnKlinikum x2, Barmherzige, Günzburg, Ilmtal, Erler, Main-Spessart, Landsberg). But app/crawl.py:518 enforces it ONLY for seeded-adapter observations, so 27106 and 46401 currently hold open Pflegefachhelfer postings and 17101 holds open ausbildung rows. Same policy, opposite outcome depending on which code path the row took.

The pflegehelfer question is a product decision for Ivan before it is a code change: are Pflegehelfer/Pflegefachhelfer in scope for this board or not. Whichever way, it needs to be enforced in one place on every path.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The Hygienefachkraft drop is traced end to end and the real cause named with file:line -- the row is in-policy and the adapter returns it, so something between adapter and database discards it
- [ ] #2 'OP Leitung' and section_labels-rescued titles like 'Onkologische Fachkraft' classify correctly, and the MFA pattern matches its inflected forms
- [ ] #3 The pflegehelfer/ausbildung scope decision is recorded explicitly, then enforced at a single point that every ingest path passes through rather than only for seeded-adapter observations
- [x] #4 Each classifier change is pinned by a test using the exact title strings from this task
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read pflege_jobs/classify.py, patterns.json, section.py, sources/inbox.py, and TASK-95's own notes (queue rewrite) to understand the current (post-round-3) intake path before touching anything.
2. Trace the Hygienefachkraft drop (36201, 76401 x2, 76301) empirically: query production posting_observations for the exact source_urls, cross-check crawl_output/*.jsonl history, re-run classify_role on the live titles, and read the CURRENT app/crawl.py/pflege_jobs/cli.py drain path end to end to confirm whether today's code would still drop them.
3. Fix the OP Leitung gate miss and the Onkologische Fachkraft / MFA-inflection bugs in patterns.json (classify.py owns no separate logic bug here -- verified empirically); add tests pinned to the exact task title strings; mutation-test each via /tmp copies.
4. Check the CURRENT pflegehelfer/ausbildung enforcement point(s) (sinks.only_pflege + cli._process_rows, both post-TASK-95) against the claim that it 'was historically enforced on only one ingest path'; record what is actually single-pointed today vs what is stale production data; flag the scope question to Ivan, do not decide it.
5. Run targeted tests, then note evidence per acceptance criterion; leave AC#2's section_labels-rescue portion honestly unchecked where the real fix is outside classify.py/patterns.json (career_crawl.py, not owned this round).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
M8/M9 root causes, traced against the CURRENT (post-TASK-95-round-3) code, 2026-09-22.

HYGIENEFACHKRAFT DROP (AC#1) -- traced end to end, real cause named.
Empirically confirmed live: clinic 76401 (Klinikum Memmingen, blank ats_type -> generic career_crawl path) still serves TWO distinct real 'Hygienefachkraft (m/w/d)' postings today (different URLs .../hygienefachkraft-m-w-d and .../hygienefachkraft-m-w-d-589, different post dates 24.Aug/10.Sep) -- confirmed via app.crawl.raw_board_rows(clinic) run live, 0 Firecrawl credits. classify_role('Hygienefachkraft (m/w/d)','') -> ('apn_experte','apn_experte:hygienefactkraft'), confirmed in-policy, confirmed NOT the bug (classify.py already correct here). Both source_urls appear in crawl_output/run_83.jsonl through run_108.jsonl (multiple runs over weeks) as kind=jobposting, yet a live PostgREST query (posting_observations?source_ref=ilike.*hygienefachkraft-m-w-d*) returns ZERO rows for either URL -- the row was never inserted, not merged/folded (ruled out fuzzy_key/canonical_ref collapsing: posting_observations upserts on (source_id,source_ref), and canonical_ref() is only used by the offline dedupe-repair CLI, never in the live insert path). Same pattern confirmed for 36201 (Barmherzige Regensburg, typo3_jobs/career_crawl) and 76301 (Klinikum Kempten, umantis seeded-adapter): DB holds 0 plain-Hygienefachkraft rows though the board serves them.
Real cause: this is STALE PRODUCTION STATE from the pre-TASK-95 pipeline, not a bug in the code as it stands today. Before TASK-95 (2026-09-21), app/crawl.py's old _post_inbox ran classify_role and dropped non-experienced rows BEFORE insert (removed; see its current docstring at app/crawl.py:470-475), AND the Postgres pflege_jobs.inbox table enforced a 2000-row/client_id/rolling-24h write cap that made intake fail outright on every scheduled run from 2026-09-19 on (TASK-92/95 notes) -- either mechanism, or both across different runs, is sufficient to explain these rows never landing, and neither retries automatically once a row silently fails (the crawler's own dedupe only ever checks Postgres inbox/posting_observations for 'already seen', never 'previously failed'). Traced the CURRENT code (app/crawl.py:443 _enqueue_local, pflege_jobs/cli.py:438-450 _process_rows' jobposting branch) end to end: it queues every row unfiltered to local SQLite and, at drain time, gates only on NON_PROD_HOST / EXCLUDED_ROLE_CLASSES / in_bavaria -- none of which would drop these rows (verified: classify_role->apn_experte, in_bavaria('Memmingen',...)->True, host is production). TASK-95's own AC#6 is still open precisely because no real production crawl+drain has run under the new code yet (data/inbox.sqlite is ~24KB, effectively empty of real rows). Conclusion: nothing in classify.py or the current intake path needs a code change for this specific symptom -- these rows are backlog waiting on the next real scheduled crawl (or a manual crawlers/load_crawl_output.py backfill of the historical jsonl into the SQLite queue) to be picked up. Flagging for whoever runs/monitors the next scheduled crawl: verify these 4 rows (and the class of rows like them) actually land once mode=adapter runs for real.

FIXED in patterns.json (pflege_jobs/patterns.json, my owned files):
- pflege_gate (line 76): 'OP Leitung (m/w/d)' had no gate token (no 'pfleg', no hyphenated 'op-bereich'/'op-fachkr') even though the _ROLES leitung rule already recognises standalone 'Leitung'/'Leiter' as their own word via (?<![a-zäöüß])leitung\b|(?<![a-zäöüß])leiter(/in|*in|in)?\b (patterns.json:102) -- the gate blocked it before that rule ever ran. Added the identical lookbehind alternation to pflege_gate so the gate can never block what the leitung role rule would otherwise classify. Verified this does not admit non-nursing leitung titles: 'Leitung Restaurant (m/w/d), Medical Park Bad Rodach' and 'Stellvertretende AEMP-Leitung (m/w/d)' (both pinned nicht_pflege in tests/test_mech_role_class.py::test_audit_fixes) still resolve nicht_pflege, now via the nicht_pflege regex's existing 'restaurant'/'aemp' tokens at step 2 instead of the gate at step 1 -- same final answer, confirmed by running the full existing suite (198 tests across every file touching classify.py, 0 failures).
- nicht_pflege (line 77): 'medizinische/?r? fachangestellte' required an EXACT 'medizinische' immediately followed by ' fachangestellte' with only an optional slash-r, so the common dative/accusative inflection never matched at all -- 'Medizinischen Fachangestellten (m/w/d) für den ambulanten OP/Station 11 in Voll-/Teilzeit', clinic 47701's real live posting title (confirmed via PostgREST: posting_id 10156, role_class was sonstige_pflege before this fix -- 'Station 11' already passes the pflege_gate on its own via \bstation(en)?\b, so this leak needed no nursing_section_confirmed at all to reach the ROLES fallback). Changed to 'medizinische[nr]?/?r? fachangestellte[nr]?'. Also verified fixed under nursing_section_confirmed=True (the rexx-style 'competing occupation inside a confirmed section' scenario tests/test_classify_section.py already covers for the un-inflected form).
Both changes mutation-tested: reverted each line individually via the Edit tool (not git), confirmed the new pinning tests in tests/test_mech_role_class.py go red, restored, confirmed green.

'OP Leitung' and 'medizinische fachangestellte' inflection: DONE, in classify.py's owned pattern files.

'Onkologische Fachkraft (w/m/d)' section_labels-rescue (18811, Asklepios Lungenklinik Gauting): NOT a classify.py bug -- verified empirically that classify_role('Onkologische Fachkraft (w/m/d)','',nursing_section_confirmed=True) already returns ('sonstige_pflege','fallback'), i.e. classify.py correctly rescues it the moment nursing_section_confirmed is True, exactly as its docstring promises. The real gap is upstream: pflege_jobs/sources/inbox.py:54 reads nursing_section_confirmed = section.job_confirmed_nursing(p.get('section_labels')), but 'section_labels' is populated ONLY by crawlers/vendor_adapters.py's structured-API adapters (dvinci/smartrecruiters/rexx/personio/wp_jobs/mein-check-in -- confirmed by grep, 8 call sites) and pflege_jobs/sources/firecrawl_agent.py. pflege_jobs/sources/career_crawl.py -- the generic JSON-LD reader that serves both 76401 and 18811 (both blank ats_type) -- never sets a section_labels key anywhere (confirmed: zero hits for the string in that file); it only has a PAGE-LEVEL 'section-first' nav-link signal (_section_link/pick_nursing_link, career_crawl.py:382-421) that is a different, coarser mechanism than the PER-JOB department label this classifier feature was built for. So 'Onkologische Fachkraft' truly does carry a per-job department/category tag on the live page ('Kategorie: ...'), but nothing in the code path that crawls it (career_crawl.py, NOT in this task's owned files -- explicitly off-limits per the round brief) ever extracts that tag into section_labels for jobposting_to_obs to read. AC#2 left UNCHECKED for this half: the fix is a career_crawl.py change (extract a per-job category/'Kategorie:' label the same way vendor_adapters.py already does, and set payload['section_labels']), outside pflege_jobs/classify.py, pflege_jobs/patterns.json, app/data.py. Handing off to whichever agent owns career_crawl.py this wave.

PFLEGEHELFER/AUSBILDUNG SINGLE-POINT ENFORCEMENT (AC#3) -- checked what the new drain already does, as instructed, before assuming the historical bug still exists.
Confirmed: after TASK-95's rewrite, EXCLUDED_ROLE_CLASSES (patterns.json:344-349, the one source of truth) is now enforced at a SINGLE functional choke point every ingest path passes through: pflege_jobs/cli.py::_process_rows -- both its kind=='jobposting' branch (line ~443) and its kind=='observation' branch (line ~460, added by TASK-95 round 1 specifically to close this exact gap for seeded adapters) check role_class against it before a row is ever handed to EdgeSink; sinks.py::only_pflege() (called from EdgeSink.write()) re-checks the identical set as a redundant safety net. Every queue (local SQLite via cmd_inbox's local drain, and the Postgres anon-key queue via its drain) funnels through this same _process_rows. app/crawl.py:518's dedupe-existing-inbox-rows loop (the file:line the task description pointed at) is the OLD _post_inbox, which per its own current docstring no longer runs classify_role at all -- it is not a role-exclusion enforcement point today, historical or current. So the 'only enforced on one ingest path' bug is already fixed as a side effect of TASK-95's queue unification, not something this round needs to change in code.
What is NOT fixed: production rows already written under the OLD, inconsistent enforcement. Confirmed live via PostgREST: clinic 27106 and 46401 both currently hold open postings with role_class-shaped titles matching the pflegehelfer pattern (Pflegefachhelfer), and 17101 holds open ausbildung-titled rows -- these predate TASK-95 and will not self-heal (no re-crawl reprocesses existing DB rows; only a fresh classify+re-resolve would, and this round may not write to production).
PRODUCT DECISION, flagged for Ivan, not decided here: patterns.json already encodes 'pflegehelfer/ausbildung are excluded' as policy (patterns.json:344-349) -- whether that is still the right call for this board is Ivan's call per the task brief. If the answer is 'exclude', the stale rows at 27106/46401/17101 need a cleanup pass (reprocessing existing posting_observations through the current classifier, or an explicit close); if the answer is 'include', patterns.json's excluded_role_classes list is the one place to change it and every ingest path already reads that same list.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
2 of 4 ACs checked with evidence, 2 left open honestly.

AC#1 CHECKED. Hygienefachkraft drop traced end to end with file:line: not a current-code bug (classify_role, in_bavaria, the host gate, app/crawl.py:443 _enqueue_local and pflege_jobs/cli.py:438-450 _process_rows all handle it correctly today, verified live) -- it is stale production state from the pre-TASK-95 pipeline (old app/crawl.py _post_inbox classify-filter, now removed, and/or the Postgres inbox's 2000-row/24h cap that failed every scheduled run from 09-19 per TASK-92/95) that has never been reprocessed under the new SQLite-queue code (TASK-95 AC#6 still open). Confirmed live for all 3 named clinics (36201 typo3, 76301 umantis-seeded, 76401 blank/career_crawl) via PostgREST + a free live board fetch: the board still serves these exact rows today, the DB still has zero matching posting_observations rows.

AC#2 NOT CHECKED, 2 of 3 fixed. 'OP Leitung (m/w/d)' and the MFA inflection ('medizinische[nr]?/?r? fachangestellte[nr]?') are fixed in pflege_jobs/patterns.json, mutation-tested, full targeted suite green (198 tests). 'Onkologische Fachkraft' / section_labels-rescue is NOT fixable in classify.py -- verified classify_role already rescues it correctly given nursing_section_confirmed=True; the real gap is that pflege_jobs/sources/career_crawl.py (the generic reader serving both 76401 and 18811) never populates payload['section_labels'] at all, unlike crawlers/vendor_adapters.py's structured adapters. Both files are outside this round's ownership (classify.py/patterns.json/app/data.py only) and explicitly off-limits per the brief. Handed off in the task notes with exact file:line for whoever owns career_crawl.py this wave.

AC#3 NOT CHECKED. The 'single point' half is true today, verified by reading the code: pflege_jobs/cli.py::_process_rows (both the jobposting and observation branches, added in TASK-95 round 1 specifically to close this gap) is the one choke point every queue drains through, backed by sinks.py::only_pflege() as a redundant net -- app/crawl.py:518 (the file:line the task pointed at) is the OLD _post_inbox, which no longer runs classify_role at all. The 'decision recorded' half is Ivan's to make, not mine -- flagged in the notes, not decided. Stale rows from before this was fixed (27106, 46401 pflegefachhelfer; 17101 ausbildung) are named but NOT cleaned up (no production writes this round, and cleanup depends on the scope decision anyway).

AC#4 CHECKED. tests/test_mech_role_class.py::test_ward_leadership_without_pflege_token (extended) pins 'OP Leitung (m/w/d)' exactly; ::test_medizinische_fachangestellte_inflected_forms_stay_excluded pins the task's named phrase plus clinic 47701's real live title (posting_id 10156, verified via PostgREST, was role_class=sonstige_pflege before this fix). Both mutation-tested: reverted each patterns.json line individually via the Edit tool, confirmed red, restored, confirmed green.

Full offline suite: not yet run (pending TASK-90/91 work in this same session); targeted suite covering every classify.py-touching test file: 198 passed, 0 failed. No production data read or written beyond GET requests (Accept-Profile: pflege_jobs) and free live HTTP board checks.
<!-- SECTION:FINAL_SUMMARY:END -->
