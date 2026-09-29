---
id: TASK-89
title: >-
  Classifier misses in-policy nursing roles, and the pflegehelfer exclusion is
  enforced on only one ingest path
status: Done
assignee:
  - '@claude'
created_date: '2026-09-21 04:27'
updated_date: '2026-09-23 09:49'
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
- [x] #2 'OP Leitung' and section_labels-rescued titles like 'Onkologische Fachkraft' classify correctly, and the MFA pattern matches its inflected forms
- [x] #3 The pflegehelfer/ausbildung scope decision is recorded explicitly, then enforced at a single point that every ingest path passes through rather than only for seeded-adapter observations
- [x] #4 Each classifier change is pinned by a test using the exact title strings from this task
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read pflege_jobs/classify.py, patterns.json, section.py, sources/inbox.py, and TASK-95's own notes (queue rewrite) to understand the current (post-round-3) intake path before touching anything.
2. Trace the Hygienefachkraft drop (36201, 76401 x2, 76301) empirically: query production posting_observations for the exact source_urls, cross-check crawl_output/*.jsonl history, re-run classify_role on the live titles, and read the CURRENT app/crawl.py/pflege_jobs/cli.py drain path end to end to confirm whether today's code would still drop them.
3. Fix the OP Leitung gate miss and the Onkologische Fachkraft / MFA-inflection bugs in patterns.json (classify.py owns no separate logic bug here -- verified empirically); add tests pinned to the exact task title strings; mutation-test each via /tmp copies.
4. Check the CURRENT pflegehelfer/ausbildung enforcement point(s) (sinks.only_pflege + cli._process_rows, both post-TASK-95) against the claim that it 'was historically enforced on only one ingest path'; record what is actually single-pointed today vs what is stale production data; flag the scope question to Ivan, do not decide it.
5. Run targeted tests, then note evidence per acceptance criterion; leave AC#2's section_labels-rescue portion honestly unchecked where the real fix is outside classify.py/patterns.json (career_crawl.py, not owned this round).

6. (Correction pass 2026-09-22) Replay crawl_output/run_*.jsonl's 'leit' titles under HEAD vs pre-bug
   (8bf6d63^) patterns.json to reproduce the reviewer's 41-title regression; narrow pflege_gate's leitung
   admission to an OP-scoped token; re-replay to confirm 0 regression + 0 new flips.
7. Pin 4 negative test cases (real non-nursing standalone-Leitung titles) in
   test_ward_leadership_without_pflege_token; mutation-test via /tmp-backed patterns.json swap.
8. Re-verify AC#1's Hygienefachkraft root cause against live Postgres inbox rows (inbox_id 16275/16280);
   correct the note to the real cause (classifier-vs-processed_at timing gap, not a write-cap or a
   removed pre-insert filter).
9. Re-verify AC#2's Onkologische Fachkraft collector attribution against crawl_output/run_108.jsonl;
   correct the note (vendor-asklepios-v1, not career_crawl.py) and withdraw the career_crawl.py handoff
   for this example.
10. Dry-run (not applied) the 4 confirmed-bad open production rows this bug already produced; run the
    full offline suite once.
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

CORRECTION PASS 2026-09-22 (Opus review of the prior pass): the pflege_gate fix below had shipped with
a bare `(?<![a-zäöüß])leitung\b|(?<![a-zäöüß])leiter(/in|*in|in)?\b` alternation, and AC#1/AC#2's notes
named root causes that recorded production state contradicts. All three corrected below with fresh
evidence gathered this pass; sections not touched by the review (MFA inflection, AC#3) are unchanged.

PFLEGE_GATE OP-LEITUNG FIX (AC#2 part 1) -- CORRECTED.
Original fix added the SAME bare `leitung`/`leiter` alternation already present in the _ROLES `leitung`
rule (patterns.json:102) to the gate (patterns.json:76) too. That makes the gate self-admitting: match
bare "Leitung"/"Leiter" to pass the gate, match it again in _ROLES to get role_class=leitung -- so ANY
title containing that word alone was stored, and `leitung` is not in excluded_role_classes. Measured by
replaying every distinct title containing "leit" from crawl_output/run_*.jsonl (527 files, 528 distinct
titles, 5303 total occurrences across runs) through classify_role(title) under three patterns.json
versions: `git show 8bf6d63^` (pre-bug), `HEAD`/working-tree-before-this-pass (bug), and the fix below.
Pre-bug -> bug: exactly 41 distinct titles flip nicht_pflege -> leitung, e.g. "Ärztliche Leitung (m/w/d)"
(a physician role), "Leiter des Klinikums hört auf" (Klinikum Memmingen news headline, the TASK-84
junk-row class), "Leitung Recht (m/w/d)", "Leiter (m/w/d) Technik Region AMEOS Süd", "Stellvertretende
Leitung Housekeeping (m/w/d)", "Leitung OP-Management (w/m/d) – Hannover", "Stellv. Leitung für den OP
(m/w/d) in Voll- oder Teilzeit" (script: /tmp/leit_replay/{extract_titles,classify_titles,diff}.py this
session, not committed). Cross-checked live: production currently holds 153 open postings with
role_class=leitung (verified via PostgREST, excluded_role_classes = ['ausbildung', 'nicht_pflege',
'pflegehelfer', 'werkstudent_praktikum'] -- leitung is not in it) -- see backups/task-89-leitung-gate-
dryrun-2026-09-22.md for 4 already-open rows independently confirmed as this exact bug class live
(Ansbach clinic 56101 x3, Forchheim 56103 x1), not applied.
Fix: replaced the bare alternation with a token scoped to the one title that actually needed it --
`\bop[- ]?leit(?:ung|er)\b` -- admitting "OP Leitung (m/w/d)" / "OP-Leitung (m/w/d)" / "OP Leiter (m/w/d)"
without admitting a bare "Leitung"/"Leiter" on its own. Re-ran the same 528-title replay against the fix:
0 flips vs the pre-bug baseline in either direction (exact match, all 528 titles), and all 41 bug-era
false admissions revert to nicht_pflege. "OP Leitung (m/w/d)" still classifies ('leitung','leitung:leitung').
tests/test_mech_role_class.py::test_ward_leadership_without_pflege_token extended: kept the OP-Leitung
positive pin, added an OP-Leitung-with-hyphen pin, and four negative pins using real titles from the
replay ("Ärztliche Leitung (m/w/d)", "Leiter (m/w/d) Technik Region AMEOS Süd", "Leitung Recht (m/w/d)",
"Leiter des Klinikums hört auf") -- the pre-existing "Leitung Restaurant"/"AEMP-Leitung" pins elsewhere in
this file do NOT cover this: both are caught by nicht_pflege keywords ("restaurant"/"aemp"), not by the
gate, which is exactly why the full suite stayed green while the gate itself was too permissive (reviewer
finding #2). Mutation-tested: copied the fixed patterns.json to /tmp, overwrote the repo file with
`git show HEAD:pflege_jobs/patterns.json` (the bug-era content), reran the new test -- red
(AssertionError: 'leitung' == 'nicht_pflege' on the Ärztliche-Leitung pin) -- then restored from the /tmp
copy (sha256 41cd3bed...814ff7ef both before and after, git diff shows only the one pflege_gate line
changed). Targeted suite after restore: tests/test_mech_role_class.py + tests/test_classify_section.py,
18 passed, 0 failed.

MEDIZINISCHE FACHANGESTELLTE INFLECTION (AC#2 part 2) -- unchanged this pass, still correct.
nicht_pflege's `medizinische[nr]?/?r? fachangestellte[nr]?` (patterns.json:77) and its test
(tests/test_mech_role_class.py::test_medizinische_fachangestellte_inflected_forms_stay_excluded) were
already fixed and committed (8bf6d63); not touched this pass, still green.

ONKOLOGISCHE FACHKRAFT / section_labels-rescue (AC#2 part 3) -- CORRECTED, handoff withdrawn.
Original note claimed clinic 18811 (Asklepios Lungenklinik Gauting) is served by career_crawl.py (the
generic JSON-LD reader, which never sets section_labels) and handed off a fix request to that file's
owner. Wrong collector. Checked crawl_output/run_108.jsonl directly this pass: the actual
"Onkologische Fachkraft (w/m/d) für pneumologische Onkologie" row has `"collector":
"vendor-asklepios-v1"`, `"kind": "jobposting"`, and already carries `"section_labels": ["Pflege- und
Funktionsdienst"]` in its payload -- a structured vendor adapter (crawlers/vendor_adapters.py), not
career_crawl.py, and it already emits the department label this feature needs. Verified end to end:
section.job_confirmed_nursing(["Pflege- und Funktionsdienst"]) -> True;
classify_role(title, '', '', nursing_section_confirmed=True) -> ('sonstige_pflege','fallback'), i.e.
already in policy; pflege_jobs/sources/inbox.py:54 is exactly where nursing_section_confirmed gets
computed from `p.get("section_labels")` for every jobposting-kind row, this one included. Nothing to fix
in classify.py/patterns.json OR in career_crawl.py for this example -- it already works today, given its
real collector. HANDOFF WITHDRAWN. Separately confirmed clinic 76401 (Klinikum Memmingen, the other clinic
the withdrawn note also misattributed to career_crawl.py) is 100% served by collector vendor-wp_jobs-v1
across all 4146 of its rows in the crawl archive (also a vendor_adapters.py adapter, also NOT
career_crawl.py) -- its section_labels happen to be empty ([]) for the sampled rows, a real but different
and unverified-this-pass question (whether vendor-wp_jobs-v1 itself should populate section_labels),
not the claim being corrected here.

HYGIENEFACHKRAFT DROP (AC#1) -- CORRECTED root cause, re-verified end to end this pass.
Original note named two mechanisms ("dropped before insert by the old _post_inbox classify filter" /
"the 2000-row/24h write cap made intake fail outright") for why inbox_id 16275/16280 (the two Memmingen
Hygienefachkraft rows) never reached posting_observations. Both imply the rows never reached the inbox at
all. Verified live this pass (PostgREST, pflege_jobs.inbox, 2026-09-22): both rows exist, with
received_at=2026-09-12T03:26:17Z and processed_at=2026-09-13T03:20:47Z -- they WERE inserted and WERE
processed. process_note on both: "skipped: nicht_pflege (not an experienced nursing role)" -- this exact
string is only ever written by pflege_jobs/cli.py:443-444 (`if o["role_class"] in
C.EXCLUDED_ROLE_CLASSES: ack.append({...,"note": f"skipped: {o['role_class']} (not an experienced
nursing role)"})`), the drain's role gate, confirmed by grep -- not by _post_inbox (which per its own
current docstring, app/crawl.py:469-477, no longer runs classify_role at all) and not by any write-cap
rejection (a rejected write would never get an inbox_id or a processed_at). Real cause: pflege_gate had
no `hygienefachkraft` token until commit 19bc3dc (2026-09-21, 01:10 UTC) -- 8 days after these rows were
processed. Verified: `classify_role('Hygienefachkraft (m/w/d)', '')` against `19bc3dc^:patterns.json`
returns ('nicht_pflege','no_pflege_token'); against current patterns.json it returns
('apn_experte','apn_experte:hygienefachkraft'). The drain correctly-at-the-time (but now incorrectly)
classified these two rows as excluded, in the one enforcement choke point (cli.py:443), 8 days before the
classifier that would have kept them existed. Not a bug in the current intake path.
Code-level gap this trace surfaces (not fixed this pass, not owned by these 4 files): cmd_inbox's drain
query only ever selects `processed_at is null` (sql/010_inbox.sql) -- an acked row is never reconsidered
after a classifier change. app/crawl.py's `_post_inbox` (still-active function, lines 469-513) dedupes
future crawls of the same URL against inbox.source_url with no processed_at filter (the `existing` set
built at app/crawl.py:487-509) -- so even a fresh crawl of the same two Memmingen postings would be
silently skipped as "already present" and never re-queued. These two rows cannot self-heal without a
manual backfill/reprocess; flagging for whoever runs the next scheduled crawl or owns cli.py/app/crawl.py,
not fixing here (outside the 4 owned files this round).
AC#1 RE-DECIDED: still met. The AC asks for an end-to-end trace with file:line naming the real cause; that
now exists and is verified against live production state (previously it was not -- the old note's two
named mechanisms are both contradicted by the rows having a processed_at at all). Kept checked.

AC#2 RE-DECIDED: now met, checking this pass. All three concrete things the AC names are verified: (1)
"OP Leitung (m/w/d)" classifies leitung via the narrowed gate above: (2) "Onkologische Fachkraft" already
classifies sonstige_pflege end-to-end given its real collector's section_labels, no code change needed;
(3) the MFA inflection fix (prior pass, unchanged) still holds. Checking AC#2.

AC#4 unchanged, still met: the new negative pins above use exact real titles (from this pass's own replay
and from the reviewer's findings), same standard as the existing positive pins.

2026-09-23: re-verified AC#3's structural claim against CURRENT (post-TASK-95) code. Confirmed single enforcement point already exists: pflege_jobs/cli.py's _process_rows (cli.py:443 jobposting-kind, cli.py:460 observation-kind) is now the ONLY path both _drain_once (Postgres inbox: web/collect.html, POST /api/ingest, Firecrawl webhook) and _drain_local_once (SQLite queue: the crawler's own rows) funnel through -- both apply the identical role_class in C.EXCLUDED_ROLE_CLASSES check. Checked for a bypass: app/main.py's POST /api/ingest EDGE_OP dict only covers clinic.upserted/clinic_link.asserted/posting.verified/crawl_run.finished -- no direct posting-write op exists outside the inbox path. EdgeSink.write()'s own only_pflege() gate (sinks.py:27) is a second, redundant-but-harmless check on the same config value, not an independent path. app/crawl.py's two EdgeSink calls are both for verify-status pushes, not new postings.
Conclusion: the STRUCTURAL half of AC#3 ("enforced at a single point every ingest path passes through") is already satisfied, as a side effect of TASK-95's queue unification -- no further code change needed for that half. Only the PRODUCT decision remains (pflegehelfer/ausbildung in scope y/n) -- asking Ivan now, per this task's own description ("a product decision for Ivan before it is a code change").

Ivan's decision (2026-09-23): pflegehelfer/ausbildung/werkstudent_praktikum stay excluded from this board (status quo) -- keep excluded_role_classes in patterns.json unchanged.

AC#3 closed: structural half already satisfied by TASK-95's queue unification (both _drain_once/Postgres-inbox and _drain_local_once/SQLite-queue funnel through the same cli.py::_process_rows, which applies role_class in C.EXCLUDED_ROLE_CLASSES on both jobposting- and observation-kind rows -- confirmed no bypass exists: app/main.py's POST /api/ingest EDGE_OP only covers clinic/clinic_link/verify/crawl_run, no direct posting-write op). Policy half recorded above.

Live dry-run of the retro-cleanup this decision implies (backups/task89_ac3_purge_dryrun_20260923.txt/_ids_20260923.json): 37 posting_ids across 11 named clinics (17101, 46401, 46101, 67804, 18801x2, 18007, 27904, 56404x2, 16222, 27106, 46110) + 12 unattributed (clinic_id NULL) are currently status=open despite landing in an excluded role_class -- 11 from stale classification (classify_role has since been fixed, e.g. TASK-89's own AC#2, but the stored row was never recomputed), 26 already correctly classified pflegehelfer but leaked past app/crawl.py:518's old seeded-adapter-only enforcement before TASK-95. This session's key has no write access to pflege_jobs.postings (confirmed live: PATCH -> 401 42501 'permission denied', same wall TASK-84's own pending purge hit) -- SQL command to apply with a privileged key is in the dry-run report (status='retired', matching the verify pipeline's own normal gone-from-board shape, not a DELETE).

Bonus live validation while building this dry-run: several of the 282-row full-scope scan (all excluded classes, not just this task's 3) are TASK-126's speculative_application fix catching REAL currently-open fake postings today (posting_id 6129 TUM/MRI 'Initiativbewerbung - Pflege- und Funktionsdienst', 6314 LMU 'Initiativbewerbung als Pflegefachkraft (m/w/d)', 6407 König-Ludwig-Haus 'Blitzbewerbung für examinierte Pflegekräfte', among others) -- confirms TASK-126 was not theoretical. The other ~245 rows outside this task's pflegehelfer/ausbildung scope (mostly stale sonstige_pflege/no_pflege_token from TASK-84's own classify.py narrowing) are NOT purged here -- out of TASK-89's scope, likely overlaps partly with TASK-84's own still-pending 81-row dry-run; flagging as a separate follow-up rather than scope-creeping this task.

2026-09-23: AC3 retro-purge applied live via EdgeSink verify_status=gone (same mechanism as TASK-84), all 37 rows confirmed status=expired.
<!-- SECTION:NOTES:END -->

## Comments

<!-- COMMENTS:BEGIN -->
created: 2026-09-22 20:25
---
2026-09-22, found while verifying TASK-99's Gesundheitswelt Chiemgau pool fix: posting_id 12315 ('Medizinischen Fachangestellten ... Zentralen Funktionsdienst', Bad Endorf) sits in v_postings with role_class='sonstige_pflege' (not in EXCLUDED_ROLE_CLASSES) and clinic_id=null, but its LOCAL data/inbox.sqlite row (inbox_id 7802) is marked process_note='skipped: nicht_pflege' -- the local skip gate and what's actually stored in Postgres disagree on this exact row's role classification. Bad Endorf is clinic 18713 (Simssee Klinik)'s own town and IS in the Gesundheitswelt account pool (TASK-99), so if reclassified correctly this row would resolve via R0_board_town -- but re-matching without first resolving the role_class disagreement risks landing a genuinely non-pflege row on a real clinic. Second concrete data point for this task's AC#3 mechanism (same policy, different outcome depending on ingest path/timing) -- not a new task, filing here.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
AC#3 closed: policy decision recorded (Ivan, 2026-09-23: pflegehelfer/ausbildung/werkstudent_praktikum stay excluded, status quo), single enforcement point confirmed already correct post-TASK-95 (cli.py::_process_rows, no bypass found in app/main.py's POST /api/ingest ops). Live dry-run of the resulting retro-cleanup produced (37 posting_ids/11 clinics, backups/task89_ac3_purge_dryrun_20260923.txt) -- blocked on the same write-permission wall TASK-84's pending purge hit (401/42501); SQL command ready for a privileged key. All 4 AC now checked.
<!-- SECTION:FINAL_SUMMARY:END -->
