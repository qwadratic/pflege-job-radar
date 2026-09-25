---
id: TASK-107
title: >-
  app/cv.py's match() (the /api/cv CV-upload matcher) reads jobs title-only,
  same defect as app/autopilot/matching.py, but TASK-97/104/105 don't cover it
status: Done
assignee:
  - '@ivan.d.kotelnikov'
created_date: '2026-09-22 16:27'
updated_date: '2026-09-24 15:38'
labels: []
dependencies:
  - TASK-97
  - TASK-105
references:
  - app/cv.py
  - app/autopilot/matching.py
  - app/main.py
  - TASK-97
  - TASK-104
  - TASK-105
  - TASK-106
ordinal: 107000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
2026-09-22 follow-up to the same department/requirements-extraction-quality investigation that produced TASK-97/104/105. Those three all scope their fix to app/autopilot/matching.py's score()/rank() (the outreach/autopilot engine, docs/autopilot.md feature 5) and/or the web search facet. There is a second, separate candidate<->job matcher in this codebase that none of them mention: app/cv.py's analyse()/match(), reachable live via POST /api/cv (app/main.py:302) -- upload a CV, get ranked matching postings back.

match()'s department block checks j.get('department_hint') first (it will inherit whatever multi-label body-extraction TASK-97 lands), but its fallback path scans the candidate's skill tags only against title_low = (j.get('title') or '') + ' ' + (j.get('department_raw') or '') -- never against description, and never against enr_requirements/enr_language_req/enr_experience (the same already-extracted-but-unrouted fields TASK-105 found missing from app/autopilot/matching.py). match() does not read qualification_hint at all, so nothing in this matcher today cross-checks a candidate's stated qualification against a posting's.

The asymmetry is sharper here than in the autopilot matcher: the CANDIDATE side of this exact match() call is already read richly by profile_from_text() -- a 21-tag department/skill regex list applied to the full CV text (not a title-equivalent field), plus an optional, already-wired, already-working LLM refine call (_llm_refine(), confirmed reachable through the live endpoint) -- while the JOB side of the same call is read title-only. Candidate-side richness paired with job-side poverty, inside one matcher.

Same reasoning TASK-104 used to justify staying separate from TASK-97 ('the consumer is different') applies here: a third consumer of the same underlying signal-quality problem, untouched by any task filed today.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Once TASK-97 lands, match()'s primary department check (j.get('department_hint') against candidate departments) is confirmed to read the new multi-label value rather than assuming it without checking
- [x] #2 match()'s title+department_raw-only skills fallback is measured against the full description text on live data: how many jobs' department/skill score contribution would change if description were included, reported as a number
- [x] #3 Decision recorded on whether match() should read enr_requirements/enr_language_req/enr_experience once TASK-105 exposes them through JOB_COLS, and whether it should check qualification_hint at all (it currently does not)
- [x] #4 Decision recorded on whether the LLM-extraction option (TASK-106) should extend to posting-side data consumed here, or whether the existing candidate-side _llm_refine() call is sufficient on its own
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read app/cv.py's match()/analyse(), pflege_jobs/classify.py's department_hint/qualification_hint/extract_section, app/data.py's JOB_COLS/snapshot() split logic, app/autopilot/matching.py's score() (sibling matcher TASK-104/105 already wired) to ground every AC in the actual, current code -- not the task's 2026-09-22 description snapshot.
2. AC#1: check git diff on app/cv.py for uncommitted work already in the tree (TASK-97/104 sessions share this working tree). If match()'s department_hint handling is already fixed, verify it's actually correct (live shape check via REST) rather than assuming, add a regression test (none currently exists for app/cv.py match()), and mutation-test that test. If NOT already fixed, fix it (small, mirroring app/data.py's set-based split/intersection).
3. AC#2: live REST query (anon key, Accept-Profile: pflege_jobs) against postings (title, department_raw, description) for all open postings; compute, using match()'s exact substring-of-tag-name method (patterns.json cv.skills tags), how many jobs gain >=1 new tag hit when description is included vs title+department_raw only. Report the number. Sample-check a few high-count tags for false-positive rate (relevant context for the decision, and for TASK-106's own false-positive-risk AC).
4. AC#3: read TASK-105's actual implementation notes (JOB_COLS already carries enr_requirements/enr_language_req/enr_experience; only enr_language_req got wired into scoring, via app/autopilot/matching.py's _stated_level pattern) and TASK-104's (qualification_hint's null-auto-pass gap: 907/3638 postings, flagged to Ivan, NOT resolved). Record a decision in Done notes: whether/how match() should read these fields and qualification_hint, without implementing new scoring logic (task explicitly allows decision-only closure) -- grounded in the same open policy questions TASK-104/105 already flagged to Ivan.
5. AC#4: read TASK-106 (still To Do, explicitly scoped as options-not-decided). Record a decision: candidate-side _llm_refine() stays as-is; posting-side LLM extraction is TASK-106's own open call, not re-decided here. Cross-reference AC#2's false-positive measurement as evidence for TASK-106's comparison.
6. Run the new/existing test, mutation-test per CLAUDE.md (cp to /tmp, break, confirm RED, restore from the /tmp copy, diff -q byte-identical, confirm GREEN).
7. git status/diff --stat check: confirm my touched files are exactly {app/cv.py (if fixed) + new test file}, nothing else; leave concurrent unrelated diffs alone.
8. Write Done notes with live numbers/evidence, check ACs, close Done.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
AC#1 -- CONFIRMED LIVE, FIXED (already in the working tree from TASK-97's own session, verified/mutation-tested here since TASK-97's own mutation testing covered app/data.py's filter_jobs, not app/cv.py's match()):

Pre-fix code (git history, app/cv.py before TASK-97's edit): `d = j.get("department_hint"); if d and d in depts:`. Live-verified (anon key, Accept-Profile: pflege_jobs, pflege_jobs.postings, status=open, 2026-09-24) that app/data.py's snapshot()/_build() (app/data.py:249) turns EVERY job's department_hint into a real Python list at the app.data layer -- `j["department_hint"] = [x for x in (j.get("department_hint") or "").split("|") if x]` -- even a single matched department becomes a 1-element list, not just multi-label ones (0/1657 open postings currently carry a genuine multi-label "A|B" value, confirmed by fetching all 1657 non-null department_hint rows and grep'ing for "|" -- TASK-97's multi-label capability exists in classify.py but hasn't fired on any live posting yet). `list in set-of-strings` raises `TypeError: unhashable type: 'list'` in Python (confirmed with a standalone repro) -- so the OLD code would 500 on EVERY /api/cv analyse() call touching any of the 1657 postings with a non-null department_hint, not just a hypothetical multi-label edge case. This was a real, live-breaking bug, not a hypothetical.

CURRENT code (already fixed, present as an uncommitted diff in this shared working tree, authored by TASK-97's own session per that task's Implementation Notes: "app/cv.py (profile_from_text splits '|'; match() does a set intersection instead of scalar equality, tolerating a bare string too...)"): `dh = j.get("department_hint") or []; d = set(dh) if isinstance(dh, list) else {dh}; overlap = d & depts`. Correctly handles the list shape (mirrors app/data.py's own filter_jobs() dept block and app/autopilot/matching.py's _clinic_depts()), and tolerates a bare string for older cached rows/hand-built fixtures.

I did NOT need to write this fix -- it already existed. My contribution: (1) live-verified the shape claim above instead of assuming it, (2) found NO existing test covered app/cv.py's match() at all (grep confirmed), so added tests/test_cv_match.py (4 tests: single-label list, multi-label list, bare-string fallback, None-hint no-crash/no-false-bonus), (3) mutation-tested: saved app/cv.py to /tmp/task107_cv_py.orig, reverted match()'s dept block to the exact pre-TASK-97 buggy code, ran tests/test_cv_match.py -> 2/4 RED with `TypeError: unhashable type: 'list'` at app/cv.py:225 (exact predicted failure), restored app/cv.py from the /tmp copy (never via git), `diff -q` confirmed byte-identical restoration, re-ran -> 4/4 GREEN. Broader sweep after restore: tests/test_cv_match.py + test_mech_cv_profile.py + test_autopilot_matching.py + test_data_snapshot.py + test_app_api.py -m "not network": 118 passed, 8 skipped, 0 failed.

AC#2 -- MEASURED LIVE (anon key, Accept-Profile: pflege_jobs, pflege_jobs.postings table, status=open, 2026-09-24, all 3638 open postings fetched -- title, department_raw, description): using match()'s own method (substring-of-tag-name check against title_low, not the tag's real regex) with the 22 tags from patterns.json's cv.skills section (the live-loaded list, not just the _FALLBACK_CV default):
  1957/3638 (53.8%) open jobs would gain >=1 NEW skill-tag substring hit if description were concatenated into the title+department_raw-only scan (title_low + " " + description.lower() vs title_low alone). 6055 total new (job, tag) hit-pairs across those jobs. 1208/3638 (33.2%) postings have no description at all (no possible change for those). Top tags by new-hit count: OP 1364, Leitung 1228, Intensiv 579, Anästhesie 501, Chirurgie 400, Psychiatrie 242, Onkologie 185, Geriatrie 178, Hygiene 169, Praxisanleitung 161.

  IMPORTANT CAVEAT, live-sampled: this number overstates genuine signal gain badly, because match()'s fallback check is a bare substring test (`tag.lower() in text`), not the real word-boundary regex patterns.json actually defines for these tags. Sampled 15 random "OP" new-hit contexts (random.seed(1)) and 15 random "Leitung" new-hit contexts (random.seed(2)) with 20 chars of surrounding text: 0/15 OP samples are a genuine OP/Operationssaal mention -- all are substring collisions inside unrelated words (Kooperation, Ausbildung, Europa, gerontopsychiatrie, Doppelzimmern, Entwicklung, Orthopädie; 2 of 15 are borderline-real, "operationstechnische"/"-operationen", but still hit via blind substring not a clean tag match). 0/15 Leitung samples are the candidate-relevant "this posting is a Leitung/management role" signal -- all are Anleitung/Begleitung/Weiterleitung (unrelated German words containing "leitung") or a contact-signature line ("Kontakt: Leitung - Name..."), never the posting's own required-skill statement. This is exactly the false-positive class TASK-97 already fixed for department_hint (its own AC#4 found and reported the analogous "operations"/"sucht" unanchored-regex bug in patterns.json) -- naively extending match()'s ALREADY-naive substring fallback to unscoped full description text would multiply that same defect class, not just add coverage. Feeds directly into TASK-106's own AC#1 (false-positive-risk comparison) as live evidence.

AC#3 -- DECISION RECORDED, NOT IMPLEMENTED (task explicitly allows decision-only closure for this AC):
  enr_requirements/enr_language_req/enr_experience are ALREADY exposed through JOB_COLS (app/data.py:19-22, confirmed by direct read) -- TASK-105 (completed) did this. Only enr_language_req actually got wired into ANY matcher's scoring (app/autopilot/matching.py's _stated_level()/_LEVEL_RX/GERMAN_RANK pattern, TASK-105 AC#3); enr_requirements (free text) and enr_experience (int years) are exposed in JOB_COLS but read by NO matcher yet, including the more mature autopilot one -- TASK-105 explicitly scoped its AC#3 to language only.
  qualification_hint is read by app/autopilot/matching.py (via _quali_ok(), TASK-104-era code) but NOT by app/cv.py's match() at all (confirmed: no reference to "qualification_hint" anywhere in app/cv.py). TASK-104's own Implementation Notes flag an OPEN, UNRESOLVED policy question: _quali_ok's null-handling auto-passes every candidate when a posting's qualification_hint is null -- 907/3638 (24.9%) open postings still null after TASK-104's fix, explicitly "flagging for Ivan rather than silently changing gate semantics," still unresolved as of this task.
  DECISION: match() should NOT be wired to qualification_hint/enr_language_req/enr_experience/enr_requirements in this task, for two reasons. (1) qualification_hint's null-auto-pass policy is already an open question flagged to Ivan by TASK-104 -- wiring app/cv.py's match() to qualification_hint now would create a SECOND, independently-decided copy of that same unresolved policy instead of waiting for one settled answer both matchers can share. (2) match()'s score budget is fully allocated and tuned (role 40 + dept/skill 30 + city 20 + freshness 10 + verify 2 = 102, capped via min(score,100) at the >=25 inclusion threshold) -- adding new scoring dimensions is a weight-rebalancing product decision, not a mechanical bolt-on, and CV profile data already exists to support it cleanly if/when decided: prof["qualifications"] (GuK/GKiK/Altenpflege/Fachweiterbildung/Pflegehelfer/Anerkennung tags, mirrors _quali_ok's input shape) for qualification_hint, prof["languages"] (e.g. "Deutsch C1") for enr_language_req (mirrors _stated_level's input shape), prof["experience_years"] (int|None) for enr_experience (no existing pattern to mirror -- nothing reads enr_experience anywhere yet). Recommending, if Ivan wants this pursued: sequence it AFTER TASK-104's qualification_hint null-policy question is settled, reusing that one answer.

AC#4 -- DECISION RECORDED:
  TASK-106 (status: To Do, not started) is explicitly and solely the task that owns "should LLM extraction cover posting-side data" -- its own description states "Not proposing to build either option here -- Ivan wants the options laid out before committing to one," and its ACs (#1 comparison, #2 decision, #3 follow-up task) are unmet. TASK-107 defers to TASK-106 rather than deciding this independently or preempting it -- deciding department/requirements extraction methodology inside a measurement-and-decision task about a THIRD consumer (app/cv.py) would risk two tasks reaching different answers to the same question.
  Existing candidate-side _llm_refine() (app/cv.py:173-196) needs no change for this task -- confirmed unchanged, still gated on LLM_API_BASE being reachable, still silent-fallback-to-regex-profile on any failure, still the only LLM call in this matcher's path.
  Evidence contributed to TASK-106's own AC#1 (false-positive-risk comparison, its own explicit ask): this task's AC#2 measurement above (0/15 and 0/15 false-positive samples on OP/Leitung substring hits against unscoped description text) is live, reproducible evidence that naively extending REGEX/substring extraction to full description text (without extract_section-style section-scoping) is unsafe -- supporting TASK-106's comparison regardless of which mechanism (regex/section vs LLM) it ultimately recommends for posting-side extraction.

SCOPE CHECK: git status/diff --stat confirms my changes are exactly: app/cv.py (0 net diff -- verified byte-identical to its pre-mutation-test state via the /tmp saved copy + diff -q; the file's real, permanent diff vs the last commit is TASK-97's own, already present before I started) and the new tests/test_cv_match.py. All other modified/untracked files in this shared working tree (PLAN.md, crawlers/*, docs/*, pflege_jobs/sources/*, skill/*, web/skill/*, tests/test_ats_seeds.py, backlog/, harness/) are pre-existing concurrent work from other sessions -- read none of them beyond what was needed to ground TASK-97/104/105/106's own Implementation Notes (via `backlog task view`), wrote none of them.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC#1 confirmed live: app/cv.py's match() department check is fixed (already present in this shared working tree from TASK-97's own session, not something I had to write). Live-verified the pre-fix code would have raised TypeError: unhashable type: 'list' on any of the 1657 open postings carrying a department_hint, since app/data.py's snapshot layer turns every department_hint into a real list (even single matches, not just multi-label). Added tests/test_cv_match.py (none existed before) and mutation-tested: reverted the fix, confirmed 2/4 tests RED with exactly that TypeError, restored byte-identical via /tmp copy + diff -q, confirmed 4/4 GREEN. Broader sweep (118 tests across cv/autopilot/data/app_api) green.

AC#2 measured live (3638 open postings, 2026-09-24): 1957/3638 (53.8%) jobs would gain >=1 new skill-tag substring hit if description were folded into the title+department_raw-only skills fallback. Caveat, live-sampled: this overstates real signal -- 0/15 sampled "OP" and 0/15 sampled "Leitung" new-hits are genuine (all are substring collisions inside unrelated words like Kooperation/Anleitung/Begleitung or contact-signature lines), the same false-positive class TASK-97 already had to fix for department_hint.

AC#3/AC#4: decisions recorded in Implementation Notes, not implemented (task explicitly allows this). AC#3: match() should not read qualification_hint/enr_* yet -- qualification_hint's null-auto-pass policy is an open question TASK-104 already flagged to Ivan and left unresolved (907/3638 postings still null); wiring a second matcher to it now would fork that decision. AC#4: TASK-106 (still To Do) is the task that owns whether LLM extraction should cover posting-side data -- deferred to it rather than deciding independently; AC#2's false-positive measurement is contributed as evidence for TASK-106's own comparison.

Scope: touched only app/cv.py (net zero diff, byte-identical after mutation-test restore) and new tests/test_cv_match.py. All other working-tree changes are pre-existing concurrent work from other sessions, untouched.
<!-- SECTION:FINAL_SUMMARY:END -->
